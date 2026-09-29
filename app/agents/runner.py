"""Agent execution system: claims queued tasks, runs the agent, and records status, output, logs and errors.

Each task runs in a savepoint: if the agent fails, everything it wrote is rolled back (no half results);
logs are written separately and always kept.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy.orm import Session

from app.agents import live
from app.agents.base import AgentError, ApprovalPending, ApprovalRejected, RetryableError
from app.agents.context import ExecutionContext, add_log
from app.agents.permissions import PermissionDenied
from app.agents.queue import claim_next
from app.agents.registry import get_implementation, get_spec
from app.db import session_factory
from app.domain.enums import AgentStatus, TaskStatus
from app.integrations.web.http import UnsafeURLError, WebAccessError
from app.models import AgentRecord, AgentTask
from app.models.base import utcnow
from app.services.audit import audit, scrub

log = logging.getLogger(__name__)
_kick_lock = threading.Lock()


def execute(db: Session, task: AgentTask) -> TaskStatus:
    """Run one task that is already RUNNING (claimed)."""
    spec, impl = get_spec(task.agent_id), get_implementation(task.agent_id)
    record = db.get(AgentRecord, task.agent_id)
    record.status = AgentStatus.RUNNING
    record.current_task_id = task.id
    add_log(db, task.id, task.agent_id, "task.started", f"{spec.name} start: {task.title}",
            data={"attempt": task.attempts, "input": task.input})
    db.commit()
    ctx = ExecutionContext(db, spec, task)
    final = ("task.failed", "Onbekende afloop", "ERROR")
    savepoint = db.begin_nested()
    try:
        output = impl.run(ctx)
        savepoint.commit()
        task.output = output
        task.status = TaskStatus.COMPLETED
        task.error = None
        record.status = AgentStatus.IDLE
        record.last_error = None
        final = ("task.completed", output.get("summary", "Taak afgerond") if isinstance(output, dict)
                 else "Taak afgerond", "INFO")
    except ApprovalPending:
        savepoint.commit()  # keep the approval request
        task.status = TaskStatus.WAITING_APPROVAL
        record.status = AgentStatus.WAITING
        final = ("task.paused", "Gepauzeerd tot een medewerker beslist over de goedkeuring", "INFO")
    except ApprovalRejected as exc:
        savepoint.rollback()
        task.status = TaskStatus.CANCELLED
        task.error = f"Goedkeuring afgewezen: {exc.note or 'geen toelichting'}"
        record.status = AgentStatus.IDLE
        final = ("task.cancelled", task.error, "WARNING")
    except (RetryableError, WebAccessError) as exc:
        savepoint.rollback()
        final = _fail_or_retry(task, record, f"{exc}", retryable=True)
    except (PermissionDenied, UnsafeURLError, AgentError, ValueError) as exc:
        savepoint.rollback()
        final = _fail_or_retry(task, record, f"{exc}", retryable=False)
    except Exception as exc:  # unexpected: record type + scrubbed message, never crash the worker
        savepoint.rollback()
        log.exception("agent task %s failed", task.id)
        final = _fail_or_retry(task, record, f"Onverwachte fout ({type(exc).__name__}): {scrub(str(exc))[:300]}",
                               retryable=False)
    finally:
        for entry in ctx.buffered_logs:  # written after the savepoint ended: kept on success and on failure
            db.add(entry)
        event, message, level = final
        add_log(db, task.id, task.agent_id, event, message, level=level)
        now = utcnow()
        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            task.finished_at = now
        record.last_run_at = now
        record.current_task_id = task.id if task.status == TaskStatus.WAITING_APPROVAL else None
        audit(db, f"agent.task_{task.status.value.lower()}", object_type="agent_task", object_id=task.id,
              client_id=task.client_id, details={"agent": task.agent_id, "type": task.task_type})
        db.commit()
        live.clear(task.id)
    return task.status


def _fail_or_retry(task: AgentTask, record: AgentRecord, message: str, *, retryable: bool) -> tuple[str, str, str]:
    if retryable and task.attempts < task.max_attempts:
        delay = 30 * task.attempts
        task.status = TaskStatus.QUEUED
        task.not_before = utcnow() + timedelta(seconds=delay)
        task.error = message
        record.status = AgentStatus.IDLE
        return "task.retry_scheduled", f"Tijdelijke fout, nieuwe poging over {delay} s: {message}", "WARNING"
    task.status = TaskStatus.FAILED
    task.error = message
    record.status = AgentStatus.FAILED
    record.last_error = message
    return "task.failed", message, "ERROR"


def run_pending(max_tasks: int = 25) -> int:
    """Process runnable tasks until the queue is empty or max_tasks is reached."""
    done = 0
    with session_factory()() as db:
        while done < max_tasks:
            task = claim_next(db)
            if task is None:
                break
            execute(db, task)
            done += 1
    return done


def kick() -> None:
    """Run the queue in-process (called in the background after tasks are created or approved)."""
    if not _kick_lock.acquire(blocking=False):
        return  # a runner is already busy in this process; it will pick up the new task
    try:
        run_pending()
    finally:
        _kick_lock.release()
