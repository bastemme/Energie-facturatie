from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.db_types import Money, Percentage
from app.domain.enums import LeadStatus, Role
from app.models.base import IdMixin, TimestampMixin, enum_type


class Client(IdMixin, TimestampMixin, Base):
    __tablename__ = "clients"

    company_name: Mapped[str] = mapped_column(String(200))
    kvk_number: Mapped[str | None] = mapped_column(String(8))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    contact_phone: Mapped[str | None] = mapped_column(String(50))
    number_of_locations: Mapped[int | None] = mapped_column(Integer)
    approx_annual_energy_spend: Mapped[Decimal | None] = mapped_column(Money)
    suppliers: Mapped[str | None] = mapped_column(String(500))  # free text, comma-separated
    invoice_period_description: Mapped[str | None] = mapped_column(String(200))
    contract_information: Mapped[str | None] = mapped_column(Text)

    # Commercial agreement — required, no hardcoded default
    success_fee_percentage: Mapped[Decimal] = mapped_column(Percentage)

    # Consent / authorization (machtiging) — stored with text version for auditability
    consent_given: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_text_version: Mapped[str | None] = mapped_column(String(50))
    consent_given_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_given_by: Mapped[str | None] = mapped_column(String(200))

    # Recoveries are computed excl. VAT. If the client deducts VAT (voorbelasting), VAT overcharges
    # have no net financial effect and are reported with potential recovery 0.
    vat_deductible: Mapped[bool] = mapped_column(Boolean, default=True)

    # Privacy
    ai_processing_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=365)
    engagement_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    users: Mapped[list["User"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(enum_type(Role))
    client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    client: Mapped[Client | None] = relationship(back_populates="users")

    @property
    def is_staff(self) -> bool:
        return self.role in (Role.ADMIN, Role.REVIEWER)


class Lead(IdMixin, TimestampMixin, Base):
    """Inbound lead from the landing page or a referral partner. Not a client yet."""

    __tablename__ = "leads"

    company_name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    kvk_number: Mapped[str | None] = mapped_column(String(8))
    approx_annual_energy_spend: Mapped[Decimal | None] = mapped_column(Money)
    number_of_locations: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50), default="website")  # website, referral, partner, ...
    referral_partner: Mapped[str | None] = mapped_column(String(200))
    utm_source: Mapped[str | None] = mapped_column(String(100))
    utm_medium: Mapped[str | None] = mapped_column(String(100))
    utm_campaign: Mapped[str | None] = mapped_column(String(100))
    privacy_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[LeadStatus] = mapped_column(enum_type(LeadStatus), default=LeadStatus.NEW)
    converted_client_id: Mapped[str | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"))
