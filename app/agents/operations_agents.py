"""Invoice, recovery and platform agents. Thin wrappers around the existing deterministic services
(app/services/operations.py and friends): they add permissions, logging, approvals and handoffs, never new maths.

    Invoice Intake → Invoice Analysis → Audit → (specialist reviews findings) → Recovery → Claims
    Customer Success · Finance · Analytics · QA · Orchestrator
"""

from __future__ import annotations

from sqlalchemy import select

from app.agents.base import Agent, AgentError
from app.agents.catalog import AGENTS
from app.agents.context import ExecutionContext
from app.domain.enums import ApprovalStatus
from app.domain.money import format_eur
from app.models import AgentApproval
from app.services import operations
from app.services.cases import CaseError


def _spec(agent_id: str):
    return next(a for a in AGENTS if a.id == agent_id)


def _client(ctx: ExecutionContext):
    client_id = ctx.input.get("client_id") or ctx.task.client_id
    if not client_id:
        raise AgentError("Geen klant opgegeven.")
    try:
        return ctx.use("clients.load", client_id=client_id)
    except ValueError as exc:
        raise AgentError(str(exc)) from exc


def _continue(ctx: ExecutionContext) -> bool:
    return bool(ctx.task.workflow_id) and ctx.input.get("continue") is not False


def _next(ctx: ExecutionContext, agent_id: str, task_type: str, payload: dict, title: str):
    return ctx.handoff(agent_id, task_type, payload, title=title, client_id=payload.get("client_id"))


class InvoiceIntake(Agent):
    spec = _spec("invoice_intake")

    def run(self, ctx: ExecutionContext) -> dict:
        client = _client(ctx)
        r = ctx.use("invoices.intake", client=client, document_ids=ctx.input.get("document_ids"))
        counts = ", ".join(f"{n} {operations.STATUS_NL.get(k, k).lower()}" for k, n in sorted(r.counts.items()))
        for d in r.attention:
            ctx.log("document.attention", f"{d.original_filename}: {operations.STATUS_NL.get(d.status.value)}",
                    level="WARNING")
        child = None
        if r.invoices and _continue(ctx):
            child = _next(ctx, "invoice_analysis", "analyse_client", {"client_id": client.id},
                          f"Analyseer facturen van {client.company_name}")
        summary = f"{client.company_name}: {sum(r.counts.values())} documenten ({counts or 'geen'}); " \
                  f"{r.invoices} facturen uitgelezen"
        if r.attention:
            summary += f"; {len(r.attention)} vragen handwerk"
        return {"summary": summary, "invoices": r.invoices, "processed_now": len(r.processed_now),
                "handoff_task_id": child.id if child else None,
                "sections": [operations.section("Documenten die aandacht vragen", ["Document", "Status", "Link"],
                                                [[d.original_filename, operations.STATUS_NL.get(d.status.value),
                                                  f"/app/documents/{d.id}"] for d in r.attention])]
                if r.attention else []}


class InvoiceAnalysis(Agent):
    spec = _spec("invoice_analysis")

    def run(self, ctx: ExecutionContext) -> dict:
        client = _client(ctx)
        run = ctx.use("analysis.run", client=client)
        for e in run.errors or []:
            ctx.log("rule.error", f"Controleregel {e.get('rule_id')} kon niet draaien", level="WARNING", data=e)
        child = None
        if _continue(ctx):
            child = _next(ctx, "audit", "audit_findings", {"client_id": client.id},
                          f"Interne controle bevindingen {client.company_name}")
        return {"summary": f"{run.invoices_analyzed} facturen gecontroleerd: {run.findings_total} bevindingen, "
                           f"waarvan {run.findings_new} nieuw" + (f"; {len(run.errors)} regel(s) met fout"
                                                                   if run.errors else ""),
                "analysis_run_id": run.id, "findings": run.findings_total, "new": run.findings_new,
                "handoff_task_id": child.id if child else None}


class AuditAgent(Agent):
    spec = _spec("audit")

    def run(self, ctx: ExecutionContext) -> dict:
        client = _client(ctx)
        results, checked = ctx.use("findings.audit", client=client)
        flagged = [(a, issues) for a, issues in results if issues]
        ctx.task.client_id = ctx.task.client_id or client.id
        ctx.shared("client").set("audit.last", {"checked": checked, "flagged": len(flagged),
                                                "findings": {a.id: issues for a, issues in flagged}})
        ready = operations.confirmed_unassigned(ctx.db, client.id)
        open_review = sum(1 for a, _ in results if a.review_status.value != "CONFIRMED")
        child = None
        if ready and _continue(ctx):
            child = _next(ctx, "recovery", "prepare_case", {"client_id": client.id},
                          f"Dossiers voorbereiden voor {client.company_name}")
        summary = f"{checked} bevindingen gecontroleerd, {len(flagged)} met aandachtspunten"
        summary += f"; {open_review} wachten op beoordeling door een specialist" if open_review else ""
        return {"summary": summary, "checked": checked, "flagged": len(flagged), "awaiting_review": open_review,
                "handoff_task_id": child.id if child else None,
                "sections": [operations.section("Aandachtspunten per bevinding", ["Bevinding", "Probleem", "Link"],
                                                [[a.title, "; ".join(i), f"/app/anomalies/{a.id}"]
                                                 for a, i in flagged])] if flagged else []}


class RecoveryAgent(Agent):
    spec = _spec("recovery")

    def run(self, ctx: ExecutionContext) -> dict:
        client = _client(ctx)
        cases, skipped = ctx.use("cases.prepare", client=client)
        for s in skipped:
            ctx.log("case.skipped", s, level="WARNING")
        children = []
        if _continue(ctx):
            for c in cases:
                children.append(_next(ctx, "claims", "draft_claim", {"case_id": c.id, "client_id": client.id},
                                      f"Claimbrief {c.reference} ({c.supplier})").id)
        return {"summary": f"{len(cases)} dossier(s) aangemaakt" + (f"; {len(skipped)} overgeslagen" if skipped
                                                                    else "") if cases or skipped else
                           "Geen bevestigde bevindingen buiten een dossier",
                "cases": [c.id for c in cases], "handoff_task_ids": children,
                "sections": [operations.section("Nieuwe dossiers", ["Dossier", "Leverancier", "Betwist bedrag",
                                                                    "Link"],
                                                [[c.reference, c.supplier, format_eur(c.disputed_amount),
                                                  f"/app/cases/{c.id}"] for c in cases])] if cases else []}


class ClaimsAgent(Agent):
    spec = _spec("claims")

    def run(self, ctx: ExecutionContext) -> dict:
        case = ctx.use("cases.load", case_id=ctx.input.get("case_id", ""))
        if case is None:
            raise AgentError("Dossier niet gevonden.")
        if ctx.task.task_type == "submit_claim":
            ctx.guard_sensitive("claims.submit", f"Claim {case.reference} bij {case.supplier} als ingediend "
                                "registreren. Bevestig dat het claimpakket naar de leverancier is (of wordt) "
                                "verstuurd.", {"case_id": case.id})
            try:
                ctx.use("claims.submit", case=case, approver_note="Indiening goedgekeurd via AI Operations")
            except CaseError as exc:
                raise AgentError(str(exc)) from exc
            return {"summary": f"Dossier {case.reference} geregistreerd als ingediend bij {case.supplier}",
                    "case_id": case.id}
        letter = ctx.use("claims.draft_letter", case=case)
        note = "; dossier staat nu op 'Claim voorbereid'" if letter["status_changed"] else \
            f"; dossier staat nog op '{case.status.value}' (eerst verifiëren)"
        return {"summary": f"Claimbrief {case.reference} opgesteld{note}", "case_id": case.id,
                "subject": letter["subject"], "letter": letter["body"]}


class CustomerSuccess(Agent):
    spec = _spec("customer_success")

    def run(self, ctx: ExecutionContext) -> dict:
        client = _client(ctx)
        approved = ctx.db.scalar(select(AgentApproval).where(AgentApproval.task_id == ctx.task.id,
                                                             AgentApproval.action == "email.send",
                                                             AgentApproval.status == ApprovalStatus.APPROVED))
        mail = (approved.details if approved else None) or ctx.use("clients.status_update", client=client)
        if mail is None:
            return {"summary": f"Geen update verstuurd: er is geen e-mailadres vastgelegd bij {client.company_name}",
                    "sent": False}
        # Approved text is sent exactly as shown in the approval request.
        ctx.guard_sensitive("email.send", f"Statusupdate aan {mail['to']}: {mail['subject']}", mail)
        sent = ctx.use("email.send", to_email=mail["to"], to_name=mail.get("to_name"), subject=mail["subject"],
                       body=mail["body"])
        return {"summary": ("Verstuurd" if sent.live else "MOCK verstuurd (niet echt verzonden)")
                + f" aan {mail['to']}", "sent": True, "live": sent.live, "subject": mail["subject"]}


class FinanceAgent(Agent):
    spec = _spec("finance")

    def run(self, ctx: ExecutionContext) -> dict:
        report = ctx.use("finance.metrics")
        for w in report["warnings"]:
            ctx.log("finance.warning", w, level="WARNING")
        ctx.shared("global").set("finance.last_report", {k: report[k] for k in ("recovered", "success_fee",
                                                                                 "payout")})
        return report


class AnalyticsAgent(Agent):
    spec = _spec("analytics")

    def run(self, ctx: ExecutionContext) -> dict:
        report = ctx.use("analytics.metrics")
        ctx.shared("global").set("analytics.last_summary", report["summary"])
        return report


class QAAgent(Agent):
    spec = _spec("qa")

    def run(self, ctx: ExecutionContext) -> dict:
        return ctx.use("qa.review_output", days=int(ctx.input.get("days", 7)))


class Orchestrator(Agent):
    """Turns a goal into tasks for the right agents. Goals: daily, find_leads, recover_client."""

    spec = _spec("orchestrator")

    def run(self, ctx: ExecutionContext) -> dict:
        goal = ctx.input.get("goal", "daily")
        started: list[list[str]] = []

        def start(agent_id, task_type, payload, title, workflow=None, client_id=None):
            t = ctx.handoff(agent_id, task_type, payload, title=title, workflow_id=workflow, client_id=client_id)
            started.append([f"#{t.number}", title])
            return t

        if goal == "find_leads":
            params = {k: ctx.input[k] for k in ("preset", "sectors", "area", "country", "target", "min_locations",
                                                "criteria", "require_website") if k in ctx.input}
            params.setdefault("preset", "energy_intensive")
            params.setdefault("country", "NL")
            start("lead_researcher", "research_prospects", params, f"Vind {params.get('target', 25)} leads",
                  workflow="lead_generation")
        elif goal == "recover_client":
            client = _client(ctx)
            start("invoice_intake", "intake_documents", {"client_id": client.id},
                  f"Terugvordering {client.company_name}: documenten", workflow="invoice_recovery",
                  client_id=client.id)
        elif goal == "daily":
            stuck = ctx.use("tasks.find_stuck", minutes=30)
            if stuck:
                ctx.use("tasks.requeue", task_ids=[t.id for t in stuck],
                        reason="Opnieuw ingepland door de Orchestrator (liep langer dan 30 minuten)")
                started.append(["—", f"{len(stuck)} vastgelopen taak/taken opnieuw ingepland"])
            from app.integrations.email.provider import provider_status

            if provider_status()["can_read"]:
                start("email", "fetch_inbox", {}, "Mailbox lezen")
            start("follow_up", "schedule_follow_up", {}, "Onbeantwoorde e-mails opvolgen")
            for client_id in ctx.use("clients.needing_cases"):
                start("recovery", "prepare_case", {"client_id": client_id}, "Dossiers voorbereiden",
                      workflow="invoice_recovery", client_id=client_id)
            start("qa", "review_output", {"days": 1}, "Kwaliteitscontrole van gisteren")
            start("finance", "finance_report", {}, "Financieel overzicht")
            start("analytics", "analytics_report", {}, "Prestatieoverzicht")
        else:
            raise AgentError(f"Onbekend doel '{goal}' (daily, find_leads, recover_client).")
        return {"summary": f"{len(started)} taken ingepland voor doel '{goal}'", "goal": goal,
                "sections": [operations.section("Ingepland", ["Taak", "Omschrijving"], started)]}
