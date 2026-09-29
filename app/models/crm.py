"""Sales pipeline: contacts at leads, outbound messages (always human-approved) and incoming replies.

Only business contact data is stored, each with its source and a legal basis. Nothing here is invented: a
contact exists because it was found on a public business page, entered by staff, or supplied by the person.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.enums import Confidence, OutreachKind, OutreachStatus, ReplyCategory
from app.models.base import IdMixin, TimestampMixin, enum_type, utcnow

# Order in which roles are preferred as the first point of contact for an energy-invoice audit.
ROLE_PRIORITY = {"ENERGY": 6, "FINANCE": 5, "CFO": 5, "PROCUREMENT": 4, "FACILITY": 3, "OPERATIONS": 2,
                 "MANAGEMENT": 1, "GENERAL": 0}


class Contact(IdMixin, TimestampMixin, Base):
    __tablename__ = "contacts"

    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))  # None for a general business mailbox
    role: Mapped[str | None] = mapped_column(String(200))  # as stated by the source
    role_category: Mapped[str] = mapped_column(String(20), default="GENERAL")  # key of ROLE_PRIORITY
    email: Mapped[str | None] = mapped_column(String(254))
    email_type: Mapped[str | None] = mapped_column(String(30))  # PERSONAL_BUSINESS | GENERAL_COMPANY_EMAIL
    phone: Mapped[str | None] = mapped_column(String(50))
    linkedin_url: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(40))  # website | manual | form | test
    source_url: Mapped[str | None] = mapped_column(String(500))
    source_excerpt: Mapped[str | None] = mapped_column(Text)  # the text the contact was read from
    confidence: Mapped[Confidence] = mapped_column(enum_type(Confidence), default=Confidence.LOW)
    legal_basis: Mapped[str] = mapped_column(String(200), default="Gerechtvaardigd belang (B2B, AVG art. 6.1.f)")
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    found_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    prospect: Mapped["Prospect"] = relationship(back_populates="contacts")  # noqa: F821

    @property
    def priority(self) -> int:
        return ROLE_PRIORITY.get(self.role_category, 0)

    @property
    def confidence_rank(self) -> int:
        return self.confidence.rank

    @property
    def display_name(self) -> str:
        return self.full_name or self.role or self.email or "Onbekend contact"


class OutreachMessage(IdMixin, TimestampMixin, Base):
    """An outbound e-mail. Drafted by an agent, sent only after a named staff member approved it."""

    __tablename__ = "outreach_messages"

    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    kind: Mapped[OutreachKind] = mapped_column(enum_type(OutreachKind), default=OutreachKind.INITIAL)
    status: Mapped[OutreachStatus] = mapped_column(enum_type(OutreachStatus), default=OutreachStatus.DRAFT,
                                                   index=True)
    to_email: Mapped[str | None] = mapped_column(String(254))
    to_name: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    personalization: Mapped[list | None] = mapped_column(JSON)  # the facts the draft is based on, with source
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id", ondelete="SET NULL"))
    in_reply_to_id: Mapped[str | None] = mapped_column(ForeignKey("inbound_messages.id", ondelete="SET NULL"))
    follow_up_of_id: Mapped[str | None] = mapped_column(ForeignKey("outreach_messages.id", ondelete="SET NULL"))
    decided_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery: Mapped[str | None] = mapped_column(String(20))  # smtp | outbox
    delivery_ref: Mapped[str | None] = mapped_column(String(300))  # Message-ID or local file name
    error: Mapped[str | None] = mapped_column(Text)

    prospect: Mapped["Prospect"] = relationship()  # noqa: F821
    contact: Mapped[Contact | None] = relationship()


class InboundMessage(IdMixin, Base):
    """A reply or incoming e-mail, classified by the Email agent (rules, with the matched phrases as reasons)."""

    __tablename__ = "inbound_messages"

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    prospect_id: Mapped[str | None] = mapped_column(ForeignKey("prospects.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"))
    outreach_id: Mapped[str | None] = mapped_column(String(36))  # last message we sent them (no FK: avoids a cycle)
    from_email: Mapped[str] = mapped_column(String(254))
    from_name: Mapped[str | None] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # imap | manual | test
    external_id: Mapped[str | None] = mapped_column(String(300), index=True)  # Message-ID, for deduplication
    category: Mapped[ReplyCategory | None] = mapped_column(enum_type(ReplyCategory), index=True)
    category_confidence: Mapped[Confidence | None] = mapped_column(enum_type(Confidence))
    category_reasons: Mapped[list | None] = mapped_column(JSON)
    classified_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    handled: Mapped[bool] = mapped_column(Boolean, default=False)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)

    prospect: Mapped["Prospect | None"] = relationship()  # noqa: F821


class Suppression(IdMixin, Base):
    """Do-not-contact list: an address that asked not to be contacted is never e-mailed again, from any lead."""

    __tablename__ = "suppressions"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(40))  # reply | manual
    prospect_id: Mapped[str | None] = mapped_column(ForeignKey("prospects.id", ondelete="SET NULL"))
    inbound_id: Mapped[str | None] = mapped_column(String(36))
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class LeadTask(IdMixin, TimestampMixin, Base):
    """A follow-up action for a person (plan the meeting, answer the question), created by an agent or by hand."""

    __tablename__ = "lead_tasks"

    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_agent_id: Mapped[str | None] = mapped_column(String(64))
    inbound_id: Mapped[str | None] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(30), default="follow_up")  # meeting | answer | follow_up | research

    prospect: Mapped["Prospect"] = relationship()  # noqa: F821

    @property
    def is_open(self) -> bool:
        return self.done_at is None


class ProspectEvent(IdMixin, Base):
    """Timeline of a lead: stage changes, contacts found, messages, notes."""

    __tablename__ = "prospect_events"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # stage, qualified, contact, outreach, reply, note, research
    message: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    agent_id: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(String(36))
    data: Mapped[dict | None] = mapped_column(JSON)


__all__ = ["ROLE_PRIORITY", "Contact", "InboundMessage", "LeadTask", "OutreachMessage", "ProspectEvent", "Suppression"]
