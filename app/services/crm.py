"""Sales pipeline operations shared by the agents and the UI: stages, qualification, contacts, outreach
messages (always human-approved), replies, and do-not-contact handling. Every change is written to the lead's
timeline."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domain.enums import (
    ApprovalStatus,
    Confidence,
    LeadStage,
    OutreachKind,
    OutreachStatus,
    Qualification,
    ReplyCategory,
)
from app.domain.replies import LABELS_NL as REPLY_LABELS_NL
from app.domain.replies import ReplyClassification
from app.models import (
    AgentApproval,
    Contact,
    InboundMessage,
    Lead,
    LeadTask,
    OutreachMessage,
    Prospect,
    ProspectEvent,
    Suppression,
    User,
)
from app.models.base import utcnow

OPEN_MESSAGE = (OutreachStatus.DRAFT, OutreachStatus.PENDING_APPROVAL, OutreachStatus.APPROVED,
                OutreachStatus.SCHEDULED)


STAGE_NL = {LeadStage.NEW: "Nieuw", LeadStage.RESEARCHING: "In onderzoek", LeadStage.QUALIFIED: "Gekwalificeerd",
            LeadStage.CONTACTED: "Benaderd", LeadStage.RESPONDED: "Gereageerd", LeadStage.MEETING: "Gesprek",
            LeadStage.PILOT: "Pilot", LeadStage.CUSTOMER: "Klant", LeadStage.REJECTED: "Afgewezen"}
QUALIFICATION_NL = {Qualification.STRONG: "sterk", Qualification.GOOD: "goed", Qualification.WEAK: "zwak",
                    Qualification.UNQUALIFIED: "niet geschikt"}
KIND_NL = {OutreachKind.INITIAL: "eerste e-mail", OutreachKind.FOLLOW_UP: "opvolgbericht",
           OutreachKind.REPLY: "antwoord"}
REPLY_NL = REPLY_LABELS_NL
CONFIDENCE_NL = {Confidence.HIGH: "hoge zekerheid", Confidence.MEDIUM: "gemiddelde zekerheid",
                 Confidence.LOW: "lage zekerheid"}


class CrmError(Exception):
    pass


def add_event(db: Session, prospect_id: str, kind: str, message: str, *, user: User | None = None,
              agent_id: str | None = None, task_id: str | None = None, data: dict | None = None) -> None:
    db.add(ProspectEvent(prospect_id=prospect_id, kind=kind, message=message[:1000], user_id=user.id if user else None,
                         agent_id=agent_id, task_id=task_id, data=data))


def set_stage(db: Session, p: Prospect, stage: LeadStage, *, reason: str | None = None, user: User | None = None,
              agent_id: str | None = None, task_id: str | None = None, forward_only: bool = False) -> bool:
    """Move a lead to `stage`. With forward_only, never moves back (agents use this; people may move freely)."""
    current = p.stage
    if current == stage:
        return False
    if forward_only and (current in (LeadStage.CUSTOMER, LeadStage.REJECTED) or stage.rank < current.rank):
        return False
    p.stage = stage
    add_event(db, p.id, "stage", f"Status {STAGE_NL[current]} → {STAGE_NL[stage]}" + (f" ({reason})" if reason else ""),
              user=user, agent_id=agent_id, task_id=task_id, data={"from": current.value, "to": stage.value})
    return True


def qualify(db: Session, p: Prospect, grade: Qualification, reasons: list[str], *, agent_id: str | None = None,
            task_id: str | None = None) -> Prospect:
    p.qualification, p.qualification_reasons, p.qualified_at = grade, reasons, utcnow()
    add_event(db, p.id, "qualified", f"Gekwalificeerd als {QUALIFICATION_NL[grade]}", agent_id=agent_id,
              task_id=task_id, data={"reasons": reasons})
    if grade in (Qualification.STRONG, Qualification.GOOD):
        set_stage(db, p, LeadStage.QUALIFIED, reason="gekwalificeerd", agent_id=agent_id, task_id=task_id,
                  forward_only=True)
        if not p.primary_contact:
            p.next_action = "Beslisser zoeken"
    return p


def save_contact(db: Session, p: Prospect, *, full_name: str | None, role: str | None, role_category: str,
                 email: str | None, email_type: str | None, linkedin_url: str | None, source: str,
                 source_url: str | None, excerpt: str | None, confidence: Confidence, is_test_data: bool = False,
                 agent_id: str | None = None, task_id: str | None = None, user: User | None = None) -> Contact | None:
    """Store a contact unless it is already known for this lead (same e-mail or same name)."""
    for c in p.contacts:
        if (email and c.email and c.email.lower() == email.lower()) or (
                full_name and c.full_name and c.full_name.lower() == full_name.lower()):
            return None
    c = Contact(prospect_id=p.id, full_name=full_name, role=role, role_category=role_category, email=email,
                email_type=email_type, linkedin_url=linkedin_url, source=source, source_url=source_url,
                source_excerpt=excerpt, confidence=confidence, is_test_data=is_test_data, found_by_agent_id=agent_id,
                task_id=task_id, created_by_user_id=user.id if user else None)
    p.contacts.append(c)
    db.flush()
    add_event(db, p.id, "contact", f"Contact vastgelegd: {c.display_name}" + (f" ({role})" if role and full_name
                                                                              else ""),
              user=user, agent_id=agent_id, task_id=task_id, data={"contact_id": c.id, "source": source_url})
    return c


def has_active_initial(db: Session, prospect_id: str) -> bool:
    return bool(db.scalar(select(func.count()).select_from(OutreachMessage).where(
        OutreachMessage.prospect_id == prospect_id, OutreachMessage.kind == OutreachKind.INITIAL,
        OutreachMessage.status.not_in((OutreachStatus.REJECTED, OutreachStatus.FAILED)))))


def create_message(db: Session, p: Prospect, contact: Contact | None, *, subject: str, body: str,
                   personalization: list[dict], kind: OutreachKind = OutreachKind.INITIAL,
                   agent_id: str | None = None, task_id: str | None = None, in_reply_to_id: str | None = None,
                   follow_up_of_id: str | None = None, to_email: str | None = None,
                   to_name: str | None = None) -> OutreachMessage:
    address = to_email or (contact.email if contact else None)
    if not address:
        raise CrmError("Geen e-mailadres bekend.")
    blocked = suppression_reason(db, p, contact, address)
    if blocked:
        raise CrmError(f"Niet benaderen: {blocked}.")
    if kind == OutreachKind.INITIAL and has_active_initial(db, p.id):
        raise CrmError("Er is al een eerste e-mail voor dit bedrijf (concept of verstuurd).")
    msg = OutreachMessage(
        prospect_id=p.id, contact_id=contact.id if contact else None, kind=kind,
        status=OutreachStatus.PENDING_APPROVAL, to_email=address, to_name=to_name or (contact.full_name if contact
                                                                                         else None),
        subject=subject, body=body, personalization=personalization,
        is_test_data=bool(p.is_test_data or (contact and contact.is_test_data)), created_by_agent_id=agent_id,
        task_id=task_id, in_reply_to_id=in_reply_to_id, follow_up_of_id=follow_up_of_id)
    db.add(msg)
    db.flush()
    if kind != OutreachKind.REPLY:  # for a reply, the next action comes from the classification
        p.next_action = "E-mail beoordelen en goedkeuren"
    add_event(db, p.id, "outreach", f"Concept-{KIND_NL[kind]} klaar voor goedkeuring: {subject}",
              agent_id=agent_id, task_id=task_id, data={"message_id": msg.id})
    return msg


def update_message(db: Session, msg: OutreachMessage, user: User, subject: str, body: str) -> None:
    if msg.status not in (OutreachStatus.DRAFT, OutreachStatus.PENDING_APPROVAL):
        raise CrmError("Dit bericht kan niet meer worden aangepast.")
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise CrmError("Onderwerp en tekst zijn verplicht.")
    if (subject, body) != (msg.subject, msg.body):
        msg.subject, msg.body, msg.edited = subject[:300], body, True
        add_event(db, msg.prospect_id, "outreach", "Concept aangepast", user=user, data={"message_id": msg.id})


def approve_and_send(db: Session, msg: OutreachMessage, user: User, *, subject: str | None = None,
                     body: str | None = None):
    """A named person approves; the Email agent sends. Returns the email task."""
    from app.agents.queue import enqueue

    if msg.status != OutreachStatus.PENDING_APPROVAL:
        raise CrmError("Alleen berichten die op goedkeuring wachten kunnen worden verstuurd.")
    if subject is not None and body is not None:
        update_message(db, msg, user, subject, body)
    p = db.get(Prospect, msg.prospect_id)
    blocked = suppression_reason(db, p, msg.contact, msg.to_email)
    if blocked:
        raise CrmError(f"Niet benaderen: {blocked}.")
    msg.status, msg.decided_by_id, msg.decided_at = OutreachStatus.APPROVED, user.id, utcnow()
    task = enqueue(db, "email", "send_email", {"message_id": msg.id}, title=f"Verstuur: {msg.subject[:120]}",
                   created_by_user_id=user.id, priority=7, workflow_id="lead_generation")
    db.add(AgentApproval(task_id=task.id, agent_id="email", action="email.send", status=ApprovalStatus.APPROVED,
                         summary=f"E-mail aan {msg.to_email}: {msg.subject}", details={"message_id": msg.id},
                         decided_by_id=user.id, decided_at=utcnow(), decision_note="Goedgekeurd in Outreach"))
    add_event(db, p.id, "outreach", f"Goedgekeurd door {user.email}: {msg.subject}", user=user,
              data={"message_id": msg.id, "task_id": task.id})
    p.next_action = "Wachten op reactie"
    return task


def reject_message(db: Session, msg: OutreachMessage, user: User, note: str) -> None:
    if msg.status not in (OutreachStatus.DRAFT, OutreachStatus.PENDING_APPROVAL):
        raise CrmError("Dit bericht is al verwerkt.")
    if not note.strip():
        raise CrmError("Geef een reden bij afwijzen.")
    msg.status, msg.decided_by_id, msg.decided_at, msg.decision_note = (OutreachStatus.REJECTED, user.id, utcnow(),
                                                                        note.strip()[:1000])
    add_event(db, msg.prospect_id, "outreach", f"Concept afgewezen: {note.strip()[:200]}", user=user,
              data={"message_id": msg.id})


def suppression_reason(db: Session, p: Prospect | None, contact: Contact | None, address: str | None) -> str | None:
    """Why this recipient may not be e-mailed, or None. Checked when drafting, approving and sending."""
    if p is not None and p.do_not_contact:
        return "bedrijf staat op de niet-benaderen-lijst"
    if contact is not None and contact.do_not_contact:
        return f"{contact.display_name} heeft gevraagd niet meer benaderd te worden"
    if address and db.scalar(select(Suppression.id).where(Suppression.email == address.lower())):
        return f"{address} staat op de suppressielijst"
    return None


def suppress_address(db: Session, address: str, reason: str, *, source: str, prospect_id: str | None = None,
                     inbound_id: str | None = None, user: User | None = None) -> None:
    address = address.lower().strip()
    if address and not db.scalar(select(Suppression.id).where(Suppression.email == address)):
        db.add(Suppression(email=address, reason=reason[:300], source=source, prospect_id=prospect_id,
                           inbound_id=inbound_id, created_by_user_id=user.id if user else None))
    for c in db.scalars(select(Contact).where(func.lower(Contact.email) == address)):
        c.do_not_contact = True
    for msg in db.scalars(select(OutreachMessage).where(func.lower(OutreachMessage.to_email) == address,
                                                        OutreachMessage.status.in_(OPEN_MESSAGE))):
        msg.status, msg.decision_note = OutreachStatus.REJECTED, f"Ingetrokken: {reason}"


def add_task(db: Session, p: Prospect, title: str, *, due_in_days: int, kind: str, agent_id: str | None = None,
             inbound_id: str | None = None) -> LeadTask:
    task = LeadTask(prospect_id=p.id, title=title[:300], due_at=utcnow() + timedelta(days=due_in_days), kind=kind,
                    created_by_agent_id=agent_id, inbound_id=inbound_id)
    db.add(task)
    add_event(db, p.id, "task", f"Taak aangemaakt: {title}", agent_id=agent_id)
    return task


def mark_do_not_contact(db: Session, p: Prospect, contact: Contact | None, reason: str, *, user: User | None = None,
                        agent_id: str | None = None, address: str | None = None, inbound_id: str | None = None) -> None:
    for a in {x.lower() for x in (address, contact.email if contact else None) if x}:
        suppress_address(db, a, reason, source="reply" if inbound_id else "manual", prospect_id=p.id,
                         inbound_id=inbound_id, user=user)
    if contact is not None:
        contact.do_not_contact = True
    if contact is None or not [c for c in p.contacts if not c.do_not_contact]:
        p.do_not_contact = True
    for msg in db.scalars(select(OutreachMessage).where(OutreachMessage.prospect_id == p.id,
                                                        OutreachMessage.status.in_(OPEN_MESSAGE))):
        if contact is None or msg.contact_id in (None, contact.id):
            msg.status, msg.decision_note = OutreachStatus.REJECTED, f"Afgemeld: {reason}"
    add_event(db, p.id, "stage", f"Niet meer benaderen: {reason}", user=user, agent_id=agent_id)


def find_prospect_for_email(db: Session, address: str) -> tuple[Prospect | None, Contact | None]:
    address = address.lower()
    contact = db.scalar(select(Contact).where(func.lower(Contact.email) == address))
    if contact:
        return contact.prospect, contact
    domain = address.split("@", 1)[-1]
    p = db.scalar(select(Prospect).where(or_(Prospect.domain == domain, Prospect.domain == "www." + domain)))
    return p, None


def register_inbound(db: Session, *, from_email: str, from_name: str | None, subject: str, body: str, source: str,
                     received_at: datetime | None = None, external_id: str | None = None,
                     in_reply_to: str | None = None, is_test_data: bool = False) -> InboundMessage | None:
    """Store an incoming message and link it to the conversation: first by the thread (In-Reply-To = one of our
    Message-IDs), then by the sender's address, then by the sender's company domain."""
    if external_id and db.scalar(select(InboundMessage.id).where(InboundMessage.external_id == external_id)):
        return None
    last = db.scalar(select(OutreachMessage).where(OutreachMessage.delivery_ref == in_reply_to)) \
        if in_reply_to else None
    if last is not None:
        p = db.get(Prospect, last.prospect_id)
        contact = db.scalar(select(Contact).where(Contact.prospect_id == p.id,
                                                  func.lower(Contact.email) == from_email.lower())) or last.contact
    else:
        p, contact = find_prospect_for_email(db, from_email)
    if p is not None and last is None:
        last = db.scalar(select(OutreachMessage).where(
            OutreachMessage.prospect_id == p.id, OutreachMessage.status == OutreachStatus.SENT,
        ).order_by(OutreachMessage.sent_at.desc()))
    msg = InboundMessage(from_email=from_email.lower(), from_name=from_name,
                         subject=subject[:300] or "(geen onderwerp)",
                         body=body, source=source, received_at=received_at or utcnow(), external_id=external_id,
                         prospect_id=p.id if p else None, contact_id=contact.id if contact else None,
                         outreach_id=last.id if last else None,
                         is_test_data=is_test_data or bool(p and p.is_test_data))
    db.add(msg)
    db.flush()
    if p is not None:
        add_event(db, p.id, "reply", f"E-mail ontvangen van {from_email}: {subject[:120]}", data={"inbound_id": msg.id})
        p.last_contact_at = msg.received_at
    return msg


STAGE_FOR_CATEGORY = {
    ReplyCategory.INTERESTED: (LeadStage.RESPONDED, "Gesprek inplannen"),
    ReplyCategory.MORE_INFORMATION: (LeadStage.RESPONDED, "Informatie sturen (concept klaar)"),
    ReplyCategory.MEETING_REQUEST: (LeadStage.MEETING, "Afspraak bevestigen (concept klaar)"),
    ReplyCategory.WRONG_PERSON: (None, "Juiste contactpersoon zoeken"),
    ReplyCategory.FOLLOW_UP: (LeadStage.RESPONDED, "Later opnieuw contact opnemen"),
    ReplyCategory.OUT_OF_OFFICE: (None, "Opnieuw proberen na afwezigheid"),
    ReplyCategory.NOT_INTERESTED: (LeadStage.REJECTED, None),
    ReplyCategory.UNSUBSCRIBE: (LeadStage.REJECTED, None),
    ReplyCategory.OTHER: (None, "Reactie lezen en beoordelen"),
}


TASK_FOR_CATEGORY = {  # (title, due in days, kind)
    ReplyCategory.MEETING_REQUEST: ("Gesprek inplannen met {who} ({company})", 1, "meeting"),
    ReplyCategory.INTERESTED: ("Reageren op interesse van {who}", 1, "answer"),
    ReplyCategory.MORE_INFORMATION: ("Informatie sturen aan {who}", 1, "answer"),
    ReplyCategory.WRONG_PERSON: ("Juiste contactpersoon zoeken bij {company}", 2, "research"),
    ReplyCategory.FOLLOW_UP: ("Opnieuw contact opnemen met {who}", 30, "follow_up"),
    ReplyCategory.OUT_OF_OFFICE: ("Opnieuw proberen bij {who} na afwezigheid", 10, "follow_up"),
}


def apply_classification(db: Session, inbound: InboundMessage, cls: ReplyClassification, *, agent_id: str,
                         task_id: str | None) -> None:
    inbound.category, inbound.category_confidence = cls.category, cls.confidence
    inbound.category_reasons, inbound.classified_by_agent_id = cls.reasons, agent_id
    p = db.get(Prospect, inbound.prospect_id) if inbound.prospect_id else None
    if p is None:
        return
    stage, action = STAGE_FOR_CATEGORY[cls.category]
    uncertain = cls.confidence == Confidence.LOW
    if cls.category == ReplyCategory.UNSUBSCRIBE:
        contact = db.get(Contact, inbound.contact_id) if inbound.contact_id else None
        mark_do_not_contact(db, p, contact, "afmelding per e-mail", agent_id=agent_id, address=inbound.from_email,
                            inbound_id=inbound.id)
    if stage is not None and not uncertain:
        set_stage(db, p, stage, reason=f"reactie: {REPLY_NL[cls.category]}", agent_id=agent_id, task_id=task_id,
                  forward_only=stage != LeadStage.REJECTED)
    if action:
        p.next_action = action
    task = TASK_FOR_CATEGORY.get(cls.category)
    if task is not None and not uncertain:
        title, days, kind = task
        who = inbound.from_name or inbound.from_email
        add_task(db, p, title.format(who=who, company=p.company_name), due_in_days=days, kind=kind,
                 agent_id=agent_id, inbound_id=inbound.id)
        p.next_action_at = utcnow() + timedelta(days=days)
    add_event(db, p.id, "reply", f"Reactie geclassificeerd: {REPLY_NL[cls.category]} ({CONFIDENCE_NL[cls.confidence]})",
              agent_id=agent_id, task_id=task_id, data={"inbound_id": inbound.id, "reasons": cls.reasons})


def sync_inbound_leads(db: Session) -> int:
    """Website requests (the landing-page form) become leads in the pipeline, with the person who asked as contact."""
    linked = select(Prospect.lead_id).where(Prospect.lead_id.is_not(None))
    created = 0
    for lead in db.scalars(select(Lead).where(Lead.id.not_in(linked))):
        domain = lead.email.split("@", 1)[-1].lower()
        evidence = [{"label": "Aanvraag via de website", "url": None, "retrieved_at": lead.created_at.isoformat()}]
        p = Prospect(company_name=lead.company_name[:200], sector="unknown", source="aanvraag",
                     source_ref=f"lead:{lead.id}", evidence=evidence,
                     fit_score=0, fit_reasons=[], lead_id=lead.id, kvk_number=lead.kvk_number,
                     locations_count=lead.number_of_locations, stage=LeadStage.NEW,
                     domain=None if domain in _FREEMAIL else domain, notes=lead.message,
                     is_test_data=lead.company_name.startswith("[DEMO"))
        p.next_action = "Aanvraag opvolgen"
        db.add(p)
        db.flush()
        db.add(Contact(prospect_id=p.id, full_name=lead.contact_name, role=None, role_category="GENERAL",
                       email=lead.email, email_type="PERSONAL_BUSINESS", phone=lead.phone, source="form",
                       source_excerpt="Zelf opgegeven via het aanvraagformulier", confidence=Confidence.HIGH,
                       legal_basis="Eigen aanvraag, met toestemming (AVG art. 6.1.a/b)",
                       is_test_data=p.is_test_data))
        add_event(db, p.id, "research", "Aanvraag ontvangen via de website")
        created += 1
    return created


_FREEMAIL = {"gmail.com", "hotmail.com", "outlook.com", "live.nl", "hotmail.nl", "ziggo.nl", "kpnmail.nl",
             "planet.nl", "icloud.com", "yahoo.com", "home.nl", "xs4all.nl"}
