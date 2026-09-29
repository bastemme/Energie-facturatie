"""AI Operations: dashboard, agents, tasks, approvals, research, prospects."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import live
from app.agents.approvals import ApprovalError, decide
from app.agents.lead_researcher import MAX_LIMIT, SECTORS
from app.agents.queue import cancel, enqueue, retry
from app.agents.registry import get_spec
from app.agents.runner import kick
from app.agents.tools import all_tools
from app.config import get_settings
from app.db import get_db
from app.domain.enums import ProspectStatus, TaskStatus
from app.integrations.web.research import get_research_provider
from app.models import AgentApproval, AgentLog, AgentMessage, AgentRecord, AgentTask, Prospect, User
from app.services.agent_ops import agent_state, dashboard, pulse
from app.services.audit import audit
from app.web.deps import client_ip, flash, redirect, render
from app.web.security import require_admin, require_staff, verify_csrf
from app.workflows.catalog import WORKFLOWS

router = APIRouter()


def _schedule(background: BackgroundTasks) -> None:
    if get_settings().agents_autorun:
        background.add_task(kick)


@router.get("/app/ops")
def ops_dashboard(request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    return render(request, "ops/dashboard.html", user=user, d=dashboard(db), pulse=pulse(db))


@router.get("/app/ops/pulse")
def ops_pulse(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    return JSONResponse({"v": pulse(db)}, headers={"Cache-Control": "no-store"})


@router.post("/app/ops/run", dependencies=[Depends(verify_csrf)])
def ops_run(request: Request, background: BackgroundTasks, user: User = Depends(require_staff)):
    background.add_task(kick)
    flash(request, "De wachtrij wordt verwerkt.", "info")
    return redirect("/app/ops")


# ---------------------------------------------------------------- agents


@router.get("/app/ops/agents/{agent_id}")
def agent_detail(agent_id: str, request: Request, user: User = Depends(require_staff),
                 db: Session = Depends(get_db)):
    agent = db.get(AgentRecord, agent_id)
    if agent is None:
        raise HTTPException(status_code=404)
    tasks = db.scalars(select(AgentTask).where(AgentTask.agent_id == agent_id)
                       .order_by(AgentTask.created_at.desc()).limit(25)).all()
    stats = dict(db.execute(select(AgentTask.status, func.count()).where(AgentTask.agent_id == agent_id)
                            .group_by(AgentTask.status)).all())
    logs = db.scalars(select(AgentLog).where(AgentLog.agent_id == agent_id).order_by(AgentLog.created_at.desc())
                      .limit(30)).all()
    tools = all_tools()
    spec = get_spec(agent_id)
    workflows = [w for w in WORKFLOWS.values() if any(s.agent_id == agent_id for s in w.stages)]
    return render(request, "ops/agent.html", user=user, agent=agent, state=agent_state(agent), tasks=tasks,
                  stats={k.value if hasattr(k, "value") else k: v for k, v in stats.items()}, logs=logs,
                  tools=[(name, tools.get(name)) for name in agent.tools], spec=spec, workflows=workflows)


@router.post("/app/ops/agents/{agent_id}/toggle", dependencies=[Depends(verify_csrf)])
def agent_toggle(agent_id: str, request: Request, user: User = Depends(require_admin),
                 db: Session = Depends(get_db)):
    agent = db.get(AgentRecord, agent_id)
    if agent is None:
        raise HTTPException(status_code=404)
    agent.enabled = not agent.enabled
    audit(db, "agent.toggled", user=user, object_type="agent", object_id=None, ip=client_ip(request),
          details={"agent": agent_id, "enabled": agent.enabled})
    db.commit()
    flash(request, f"{agent.name} is {'ingeschakeld' if agent.enabled else 'uitgeschakeld'}.", "success")
    return redirect(f"/app/ops/agents/{agent_id}")


# ---------------------------------------------------------------- tasks


@router.get("/app/ops/tasks/{task_id}")
def task_detail(task_id: str, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    agent = db.get(AgentRecord, task.agent_id)
    children = db.scalars(select(AgentTask).where(AgentTask.parent_task_id == task.id)).all()
    parent = db.get(AgentTask, task.parent_task_id) if task.parent_task_id else None
    messages = db.scalars(select(AgentMessage).where((AgentMessage.source_task_id == task.id)
                                                     | (AgentMessage.created_task_id == task.id))).all()
    approvals = db.scalars(select(AgentApproval).where(AgentApproval.task_id == task.id)).all()
    prospects = []
    if task.agent_id == "lead_researcher":
        prospects = db.scalars(select(Prospect).where(Prospect.research_task_id == task.id)
                               .order_by(Prospect.fit_score.desc())).all()
    elif isinstance(task.input, dict) and task.input.get("prospect_ids"):
        prospects = db.scalars(select(Prospect).where(Prospect.id.in_(task.input["prospect_ids"]))
                               .order_by(Prospect.fit_score.desc())).all()
    workflow = WORKFLOWS.get(task.workflow_id or "")
    return render(request, "ops/task.html", user=user, task=task, agent=agent, children=children, parent=parent,
                  messages=messages, approvals=approvals, prospects=prospects, workflow=workflow,
                  names={a.id: a.name for a in db.scalars(select(AgentRecord)).all()}, sectors=SECTORS,
                  pulse=pulse(db) if task.is_open else None,
                  steps=list(task.logs) + (live.entries(task.id) if task.status == TaskStatus.RUNNING else []))


@router.post("/app/ops/tasks/{task_id}/cancel", dependencies=[Depends(verify_csrf)])
def task_cancel(task_id: str, request: Request, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    try:
        cancel(db, task, f"Geannuleerd door {user.email}")
        audit(db, "agent.task_cancelled", user=user, object_type="agent_task", object_id=task.id)
        db.commit()
        flash(request, "Taak geannuleerd.", "success")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/app/ops/tasks/{task_id}")


@router.post("/app/ops/tasks/{task_id}/retry", dependencies=[Depends(verify_csrf)])
def task_retry(task_id: str, request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
               db: Session = Depends(get_db)):
    task = db.get(AgentTask, task_id)
    if task is None:
        raise HTTPException(status_code=404)
    try:
        retry(db, task)
        audit(db, "agent.task_retried", user=user, object_type="agent_task", object_id=task.id)
        db.commit()
        _schedule(background)
        flash(request, "Taak staat opnieuw in de wachtrij.", "success")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return redirect(f"/app/ops/tasks/{task_id}")


# ---------------------------------------------------------------- approvals


@router.post("/app/ops/approvals/{approval_id}", dependencies=[Depends(verify_csrf)])
def approval_decide(approval_id: str, request: Request, background: BackgroundTasks, decision: str = Form(...),
                    note: str = Form(""), next_url: str = Form(""), user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    approval = db.get(AgentApproval, approval_id)
    if approval is None:
        raise HTTPException(status_code=404)
    try:
        decide(db, approval, user, approve=decision == "approve", note=note)
        db.commit()
        if decision == "approve":
            _schedule(background)
        flash(request, "Goedgekeurd; de agent gaat verder." if decision == "approve" else "Afgewezen.", "success")
    except ApprovalError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return redirect(next_url if next_url.startswith("/app/") else "/app/ops")


# ---------------------------------------------------------------- lead research


@router.get("/app/ops/research/new")
def research_new(request: Request, user: User = Depends(require_staff)):
    provider = get_research_provider()
    return render(request, "ops/research_new.html", user=user, sectors=SECTORS.values(), provider=provider,
                  max_limit=MAX_LIMIT, threshold=get_settings().research_approval_threshold,
                  qualify=get_settings().lead_qualify_threshold)


@router.post("/app/ops/research", dependencies=[Depends(verify_csrf)])
async def research_create(request: Request, background: BackgroundTasks, user: User = Depends(require_staff),
                          db: Session = Depends(get_db)):
    form = await request.form()
    sectors = [s for s in form.getlist("sectors") if s in SECTORS]
    area = str(form.get("area", "")).strip()
    limit = str(form.get("limit", "20")).strip()
    errors = []
    if not sectors:
        errors.append("Kies ten minste één sector.")
    if not area:
        errors.append("Vul een gemeente, plaats of provincie in.")
    if not limit.isdigit() or not 1 <= int(limit) <= MAX_LIMIT:
        errors.append(f"Kies een maximum tussen 1 en {MAX_LIMIT}.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return redirect("/app/ops/research/new")
    labels = ", ".join(SECTORS[s].label.lower() for s in sectors)
    task = enqueue(db, "lead_researcher", "research_prospects",
                   {"sectors": sectors, "area": area[:80], "limit": int(limit),
                    "require_website": form.get("require_website") == "on"},
                   title=f"Onderzoek {labels} in {area[:80]}", workflow_id="lead_generation",
                   created_by_user_id=user.id, priority=6)
    audit(db, "agent.task_created", user=user, object_type="agent_task", object_id=task.id, ip=client_ip(request),
          details={"agent": "lead_researcher"})
    db.commit()
    _schedule(background)
    flash(request, "Onderzoek gestart. U ziet de voortgang hieronder live.", "success")
    return redirect(f"/app/ops/tasks/{task.id}")


# ---------------------------------------------------------------- prospects


@router.get("/app/ops/prospects")
def prospects_list(request: Request, status: str = "", sector: str = "", user: User = Depends(require_staff),
                   db: Session = Depends(get_db)):
    q = select(Prospect).order_by(Prospect.fit_score.desc(), Prospect.created_at.desc())
    if status in ProspectStatus.__members__:
        q = q.where(Prospect.status == ProspectStatus(status))
    if sector in SECTORS:
        q = q.where(Prospect.sector == sector)
    prospects = db.scalars(q.limit(500)).all()
    return render(request, "ops/prospects.html", user=user, prospects=prospects, sectors=SECTORS,
                  filters={"status": status, "sector": sector}, qualify=get_settings().lead_qualify_threshold,
                  total=db.scalar(select(func.count()).select_from(Prospect)) or 0)


@router.get("/app/ops/prospects/{prospect_id}")
def prospect_detail(prospect_id: str, request: Request, user: User = Depends(require_staff),
                    db: Session = Depends(get_db)):
    p = db.get(Prospect, prospect_id)
    if p is None:
        raise HTTPException(status_code=404)
    return render(request, "ops/prospect.html", user=user, p=p, sector=SECTORS.get(p.sector),
                  task=db.get(AgentTask, p.research_task_id) if p.research_task_id else None,
                  qualify=get_settings().lead_qualify_threshold)



