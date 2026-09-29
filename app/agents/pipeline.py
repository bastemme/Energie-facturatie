"""Sales pipeline agents: Lead Qualifier → Contact Researcher → Outreach → (human approval) → Email → Follow-up.

All deterministic: fixed qualification rules, contacts only when a public page states them, e-mails from
templates filled with stored facts. Nothing is sent without a named person's approval.
"""

from __future__ import annotations

from datetime import timedelta

from app.agents.base import Agent, AgentError
from app.agents.catalog import AGENTS
from app.agents.context import ExecutionContext
from app.config import get_settings
from app.domain.enums import Confidence, LeadStage, OutreachKind, OutreachStatus, Qualification
from app.integrations.email.provider import EmailError
from app.integrations.web.http import UnsafeURLError, WebAccessError
from app.models import Contact, InboundMessage, Prospect
from app.models.base import utcnow
from app.services import crm, outreach_copy
from app.workflows.catalog import get_workflow


def _spec(agent_id: str):
    return next(a for a in AGENTS if a.id == agent_id)


def _ids(ctx: ExecutionContext) -> list[str]:
    ids = ctx.input.get("prospect_ids") or ([ctx.input["prospect_id"]] if ctx.input.get("prospect_id") else [])
    if not ids or not isinstance(ids, list):
        raise AgentError("Geen bedrijven opgegeven.")
    return [str(i) for i in ids][:500]


def _next(ctx: ExecutionContext, agent_id: str, ids: list[str], title: str):
    """Hand work to the next workflow stage, unless this task was started as a single step."""
    if not ids or ctx.input.get("continue") is False:
        return None
    workflow = get_workflow(ctx.task.workflow_id)
    stage = workflow.next_stage(agent_id) if workflow else None
    if stage is None:
        return None
    return ctx.handoff(stage.agent_id, stage.task_type, {"prospect_ids": ids, "source_task_id": ctx.task.id},
                       title=title)


# ---------------------------------------------------------------- Lead Qualifier


def qualify_rules(p: Prospect) -> tuple[Qualification, list[str]]:
    """Fixed, explainable qualification. Returns the grade and a reason per rule that applied."""
    order = [Qualification.UNQUALIFIED, Qualification.WEAK, Qualification.GOOD, Qualification.STRONG]
    if p.source == "aanvraag":
        return Qualification.STRONG, ["Heeft zelf een aanvraag gedaan via de website"]
    score = p.fit_score or 0
    grade = (Qualification.STRONG if score >= 70 else Qualification.GOOD if score >= 55
             else Qualification.WEAK if score >= 40 else Qualification.UNQUALIFIED)
    reasons = [f"Fitscore {score}/100: {crm.QUALIFICATION_NL[grade]}"]
    if p.brand:
        grade = order[max(0, order.index(grade) - 1)]
        reasons.append(f"Filiaal van '{p.brand}': energie wordt waarschijnlijk centraal ingekocht (één niveau lager)")
    if (p.locations_count or 0) >= 3 and grade == Qualification.GOOD:
        grade = Qualification.STRONG
        reasons.append(f"{p.locations_count} vestigingen: meer aansluitingen en facturen (één niveau hoger)")
    if not p.website and grade in (Qualification.STRONG, Qualification.GOOD):
        reasons.append("Geen website bekend: een beslisser vinden gaat handmatig")
    return grade, reasons


class LeadQualifier(Agent):
    spec = _spec("lead_qualifier")

    def run(self, ctx: ExecutionContext) -> dict:
        prospects = ctx.use("prospects.load", ids=_ids(ctx))
        counts = {g.value: 0 for g in Qualification}
        qualified, skipped = [], 0
        for i, p in enumerate(prospects, 1):
            ctx.progress(i, len(prospects))
            if p.do_not_contact or p.stage in (LeadStage.CUSTOMER, LeadStage.REJECTED):
                skipped += 1
                continue
            grade, reasons = qualify_rules(p)
            ctx.use("prospects.qualify", p=p, grade=grade, reasons=reasons, agent_id=self.spec.id,
                    task_id=ctx.task.id)
            counts[grade.value] += 1
            if grade in (Qualification.STRONG, Qualification.GOOD):
                qualified.append(p.id)
        child = _next(ctx, self.spec.id, qualified, f"Zoek beslissers bij {len(qualified)} bedrijven")
        return {"summary": f"{len(qualified)} van {len(prospects)} bedrijven gekwalificeerd (sterk of goed)",
                "grades": counts, "qualified": len(qualified), "skipped": skipped,
                "handoff_task_id": child.id if child else None, "prospect_ids": qualified}


# ---------------------------------------------------------------- Contact Researcher


class ContactResearcher(Agent):
    spec = _spec("contact_researcher")

    def run(self, ctx: ExecutionContext) -> dict:
        prospects = ctx.use("prospects.load", ids=_ids(ctx))
        stats = {"pages_read": 0, "contacts_saved": 0, "decision_makers": 0, "no_website": 0, "unreachable": 0,
                 "nothing_found": 0}
        reachable = []
        for i, p in enumerate(prospects, 1):
            ctx.progress(i, len(prospects))
            if p.do_not_contact:
                continue
            if not p.website:
                stats["no_website"] += 1
                p.next_action = "Contact handmatig zoeken (geen website bekend)"
                continue
            try:
                result = ctx.use("web.find_contacts", website=p.website, company_name=p.company_name)
            except (WebAccessError, UnsafeURLError) as exc:
                stats["unreachable"] += 1
                ctx.log("website.unreachable", f"{p.company_name}: {exc}", level="WARNING")
                continue
            stats["pages_read"] += len(result.pages_read)
            findings = sorted(result.findings, key=lambda f: -_priority(f.role_category))
            for f in findings:
                c = ctx.use("contacts.save", p=p, full_name=f.full_name, role=f.role, role_category=f.role_category,
                            email=f.email, email_type=f.email_type, linkedin_url=f.linkedin_url, source="website"
                            if not f.is_test_data else "test", source_url=f.source_url, excerpt=f.excerpt,
                            confidence=Confidence(f.confidence), is_test_data=f.is_test_data,
                            agent_id=self.spec.id, task_id=ctx.task.id)
                if c is not None:
                    stats["contacts_saved"] += 1
                    stats["decision_makers"] += bool(f.full_name and f.role_category not in ("GENERAL",))
            best = p.primary_contact
            if best is None:
                stats["nothing_found"] += 1
                p.next_action = "Contact niet gevonden: handmatig zoeken (LinkedIn, telefoon)"
                ctx.event(p.id, "contact", "Contact niet gevonden op de openbare website",
                          {"pages": result.pages_read})
            elif best.email:
                p.next_action = "E-mail opstellen"
                reachable.append(p.id)
            else:
                p.next_action = f"E-mailadres zoeken voor {best.display_name}"
        child = _next(ctx, self.spec.id, reachable, f"Stel e-mails op voor {len(reachable)} bedrijven")
        return {"summary": f"{stats['contacts_saved']} contacten vastgelegd ({stats['decision_makers']} beslissers "
                           f"met naam) bij {len(prospects)} bedrijven; {len(reachable)} per e-mail bereikbaar",
                **stats, "reachable": len(reachable), "handoff_task_id": child.id if child else None}


def _priority(category: str) -> int:
    from app.models.crm import ROLE_PRIORITY

    return ROLE_PRIORITY.get(category, 0)


# ---------------------------------------------------------------- Outreach


class OutreachAgent(Agent):
    spec = _spec("outreach")

    def run(self, ctx: ExecutionContext) -> dict:
        from app.agents.lead_researcher import SECTORS

        prospects = ctx.use("prospects.load", ids=_ids(ctx))
        drafted, skipped = [], []
        for i, p in enumerate(prospects, 1):
            ctx.progress(i, len(prospects))
            contact = _contact_for(ctx, p)
            if p.do_not_contact or p.stage in (LeadStage.CUSTOMER, LeadStage.REJECTED):
                skipped.append(f"{p.company_name}: niet benaderen")
                continue
            if contact is None or not contact.email:
                skipped.append(f"{p.company_name}: geen e-mailadres")
                continue
            sector = SECTORS.get(p.sector)
            draft = outreach_copy.initial_email(
                company=p.company_name, sector=p.sector if sector else None,
                sector_label=sector.label if sector else None, city=p.city,
                multi_location_text=(p.signals or {}).get("multi_location"), contact_name=contact.full_name,
                contact_role=contact.role, generic_mailbox=contact.email_type == "GENERAL_COMPANY_EMAIL",
                source_label=_source_label(p), source_url=(p.evidence or [{}])[0].get("url"), website=p.website)
            try:
                msg = ctx.use("outreach.draft_message", p=p, contact=contact, subject=draft.subject, body=draft.body,
                              personalization=draft.personalization, agent_id=self.spec.id, task_id=ctx.task.id)
            except crm.CrmError as exc:
                skipped.append(f"{p.company_name}: {exc}")
                continue
            drafted.append(msg.id)
        for s in skipped:
            ctx.log("outreach.skipped", s, level="INFO")
        return {"summary": f"{len(drafted)} concept-e-mails klaar voor goedkeuring"
                           + (f"; {len(skipped)} overgeslagen" if skipped else ""),
                "drafted": len(drafted), "skipped": skipped, "message_ids": drafted}


def _contact_for(ctx: ExecutionContext, p: Prospect) -> Contact | None:
    wanted = ctx.input.get("contact_id")
    if wanted:
        return next((c for c in p.contacts if c.id == wanted and not c.do_not_contact), None)
    with_email = [c for c in p.contacts if c.email and not c.do_not_contact]
    return max(with_email, key=lambda c: (c.email_type != "GENERAL_COMPANY_EMAIL", c.priority, c.confidence_rank),
               default=None)


def _source_label(p: Prospect) -> str:
    return {"openstreetmap": "OpenStreetMap", "mock": "Testdata", "aanvraag": "Aanvraag via website"}.get(
        p.source, p.source)


# ---------------------------------------------------------------- Email


class EmailAgent(Agent):
    spec = _spec("email")

    def run(self, ctx: ExecutionContext) -> dict:
        if ctx.task.task_type == "send_email":
            return self._send(ctx)
        if ctx.task.task_type == "classify_reply":
            return self._classify(ctx, ctx.db.get(InboundMessage, ctx.input.get("inbound_id", "")))
        if ctx.task.task_type == "fetch_inbox":
            return self._fetch(ctx)
        raise AgentError(f"Onbekend taaktype {ctx.task.task_type}")

    def _send(self, ctx: ExecutionContext) -> dict:
        msg = ctx.use("outreach.load_message", message_id=ctx.input.get("message_id", ""))
        if msg is None:
            raise AgentError("Bericht niet gevonden.")
        if msg.status != OutreachStatus.APPROVED:
            raise AgentError(f"Bericht heeft status {msg.status.value}; alleen goedgekeurde berichten gaan uit.")
        ctx.guard_sensitive("email.send", f"E-mail aan {msg.to_email}: {msg.subject}", {"message_id": msg.id})
        p = ctx.db.get(Prospect, msg.prospect_id)
        blocked = crm.suppression_reason(ctx.db, p, msg.contact, msg.to_email)
        if blocked:
            msg.status, msg.error = OutreachStatus.REJECTED, f"Niet verstuurd: {blocked}"
            ctx.log("email.suppressed", msg.error, level="WARNING")
            return {"summary": msg.error, "sent": False}
        in_reply_to = None
        if msg.in_reply_to_id:
            original = ctx.db.get(InboundMessage, msg.in_reply_to_id)
            in_reply_to = original.external_id if original else None
        try:
            sent = ctx.use("email.send", to_email=msg.to_email, to_name=msg.to_name, subject=msg.subject,
                           body=msg.body, in_reply_to=in_reply_to)
        except EmailError as exc:
            # The failure is recorded on the message and shown in the UI; the task itself completes.
            msg.status, msg.error = OutreachStatus.FAILED, str(exc)
            ctx.log("email.failed", str(exc), level="ERROR")
            crm.add_event(ctx.db, p.id, "outreach", f"Verzenden mislukt: {exc}", agent_id=self.spec.id,
                          task_id=ctx.task.id, data={"message_id": msg.id})
            return {"summary": f"Verzenden mislukt: {exc}", "sent": False, "error": str(exc)}
        msg.status, msg.sent_at = OutreachStatus.SENT, utcnow()
        msg.delivery, msg.delivery_ref = ("live" if sent.live else "mock"), sent.message_id
        p.last_contact_at = msg.sent_at
        if msg.kind != OutreachKind.REPLY:
            p.next_action = "Wachten op reactie"
            p.next_action_at = msg.sent_at + timedelta(days=get_settings().follow_up_days)
            crm.set_stage(ctx.db, p, LeadStage.CONTACTED, reason="e-mail verstuurd", agent_id=self.spec.id,
                          task_id=ctx.task.id, forward_only=True)
        label = "Verstuurd" if sent.live else "MOCK verstuurd (niet echt verzonden)"
        crm.add_event(ctx.db, p.id, "outreach", f"{label} aan {msg.to_email}: {msg.subject}", agent_id=self.spec.id,
                      task_id=ctx.task.id, data={"message_id": msg.id, "live": sent.live})
        if not sent.live:
            ctx.log("email.mock", "MOCK-provider: bericht als .eml opgeslagen, niet echt verzonden", level="WARNING")
        return {"summary": f"{label} aan {msg.to_email}", "sent": True, "live": sent.live}

    def _classify(self, ctx: ExecutionContext, inbound: InboundMessage | None) -> dict:
        if inbound is None:
            raise AgentError("Bericht niet gevonden.")
        cls = ctx.use("email.classify_reply", subject=inbound.subject, body=inbound.body)
        ctx.use("leads.apply_reply", inbound=inbound, cls=cls, agent_id=self.spec.id, task_id=ctx.task.id)
        draft_id = None
        p = ctx.db.get(Prospect, inbound.prospect_id) if inbound.prospect_id else None
        contact = ctx.db.get(Contact, inbound.contact_id) if inbound.contact_id else None
        draft = outreach_copy.reply_draft(cls.category, contact_name=(contact.full_name if contact else None)
                                          or inbound.from_name, subject=inbound.subject)
        if p is not None and draft is not None and not p.do_not_contact:
            msg = ctx.use("outreach.draft_message", p=p, contact=contact, subject=draft.subject, body=draft.body,
                          personalization=draft.personalization, kind=OutreachKind.REPLY, agent_id=self.spec.id,
                          task_id=ctx.task.id, in_reply_to_id=inbound.id, to_email=inbound.from_email,
                          to_name=inbound.from_name)
            draft_id = msg.id
        return {"summary": f"Geclassificeerd als {crm.REPLY_NL[cls.category]} ({crm.CONFIDENCE_NL[cls.confidence]})"
                           + ("; antwoord klaar voor goedkeuring" if draft_id else ""),
                "category": cls.category.value, "confidence": cls.confidence.value, "reasons": cls.reasons,
                "linked_to_lead": p is not None, "reply_draft_id": draft_id}

    def _fetch(self, ctx: ExecutionContext) -> dict:
        try:
            mails = ctx.use("inbox.fetch")
        except EmailError as exc:
            raise AgentError(str(exc)) from exc
        new = 0
        for m in mails:
            if m.from_email == get_settings().outreach_from_email:
                continue  # our own messages in the mailbox
            inbound = ctx.use("inbox.register", from_email=m.from_email, from_name=m.from_name, subject=m.subject,
                              body=m.body, source="imap" if mails_live(self) else "mock",
                              received_at=m.received_at, external_id=m.external_id, in_reply_to=m.in_reply_to)
            if inbound is not None:
                new += 1
                self._classify(ctx, inbound)
        return {"summary": f"{new} nieuwe berichten gelezen en geclassificeerd", "new": new, "read": len(mails)}


def mails_live(_agent=None) -> bool:
    from app.integrations.email.provider import get_email_provider

    return get_email_provider().is_live


# ---------------------------------------------------------------- Follow-up


class FollowUpAgent(Agent):
    spec = _spec("follow_up")

    def run(self, ctx: ExecutionContext) -> dict:
        days = int(ctx.input.get("older_than_days", get_settings().follow_up_days))
        unanswered = ctx.use("outreach.find_unanswered", older_than_days=days)
        drafted = []
        for m in unanswered:
            p = ctx.db.get(Prospect, m.prospect_id)
            draft = outreach_copy.follow_up_email(company=p.company_name, contact_name=m.to_name,
                                                  original_subject=m.subject)
            try:
                msg = ctx.use("outreach.draft_message", p=p, contact=m.contact, subject=draft.subject, body=draft.body,
                              personalization=draft.personalization, kind=OutreachKind.FOLLOW_UP,
                              agent_id=self.spec.id, task_id=ctx.task.id, follow_up_of_id=m.id, to_email=m.to_email,
                              to_name=m.to_name)
            except crm.CrmError as exc:
                ctx.log("follow_up.skipped", f"{p.company_name}: {exc}")
                continue
            drafted.append(msg.id)
        return {"summary": f"{len(drafted)} opvolgberichten klaar voor goedkeuring ({len(unanswered)} e-mails "
                           f"zonder reactie na {days} dagen)", "drafted": len(drafted), "message_ids": drafted}
