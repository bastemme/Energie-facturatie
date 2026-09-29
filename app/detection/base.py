"""Core types of the detection engine.

Detectors are pure functions of an AnalysisContext. They never write to the database and never
call an LLM. Every finding carries a Reconciliation (actual / expected / difference / reason /
evidence / confidence) and human-readable calculation steps.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.domain.confidence import EvidenceQuality
from app.domain.enums import Classification, Confidence, ExtractionMethod, InvoiceType, Severity
from app.domain.money import ZERO, format_decimal_nl, format_eur, format_price, round_cents
from app.models import (
    Contract,
    ContractPrice,
    ExtractedValue,
    Invoice,
    InvoiceLine,
    MeterReading,
    ReferenceRate,
)

STRUCTURED_METHODS = {ExtractionMethod.CSV, ExtractionMethod.XLSX, ExtractionMethod.MANUAL}

SEVERITY_HIGH_FROM = Decimal("500")
SEVERITY_MEDIUM_FROM = Decimal("50")


@dataclass(frozen=True)
class DetectionSettings:
    line_amount_tolerance: Decimal = Decimal("0.02")
    total_tolerance: Decimal = Decimal("0.05")
    unit_price_tolerance: Decimal = Decimal("0.000005")
    quantity_tolerance: Decimal = Decimal("0.5")
    min_potential_recovery: Decimal = Decimal("0.50")
    consumption_spike_ratio: Decimal = Decimal("2.0")
    consumption_drop_ratio: Decimal = Decimal("0.4")
    min_history_periods: int = 3
    vat_deductible: bool = True
    # Overlaps/gaps up to this many days are treated as date-convention differences ("t/m" vs "tot").
    period_boundary_tolerance_days: int = 1


@dataclass
class AnalysisContext:
    client_id: str
    invoices: list[Invoice]
    contracts: list[Contract] = field(default_factory=list)
    meter_readings: list[MeterReading] = field(default_factory=list)
    reference_rates: list[ReferenceRate] = field(default_factory=list)  # verified rows only
    provenance: dict[tuple[str, str], ExtractedValue] = field(default_factory=dict)
    settings: DetectionSettings = field(default_factory=DetectionSettings)
    # Invoices excluded from cross-invoice checks because they are data duplicates (same number)
    duplicate_invoice_ids: set[str] = field(default_factory=set)

    @property
    def active_invoices(self) -> list[Invoice]:
        return [i for i in self.invoices if i.id not in self.duplicate_invoice_ids]

    @property
    def regular_invoices(self) -> list[Invoice]:
        return [i for i in self.active_invoices if i.invoice_type == InvoiceType.INVOICE]

    @property
    def credit_notes(self) -> list[Invoice]:
        return [i for i in self.active_invoices if i.invoice_type == InvoiceType.CREDIT_NOTE]

    def credited_invoice_numbers(self) -> set[str]:
        return {c.corrects_invoice_number for c in self.credit_notes if c.corrects_invoice_number}


@dataclass
class Finding:
    rule_id: str
    rule_version: str
    category: str
    classification: Classification
    confidence: Confidence
    title: str
    description: str
    reason: str
    invoice: Invoice | None = None
    line: InvoiceLine | None = None
    actual: Decimal | None = None
    expected: Decimal | None = None
    difference: Decimal | None = None
    unit: str = "EUR"
    potential_recovery: Decimal = ZERO
    calculation: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    requires_verification: bool = True
    key: str = ""
    severity: Severity | None = None

    def __post_init__(self) -> None:
        self.potential_recovery = round_cents(max(self.potential_recovery, ZERO))
        if self.severity is None:
            self.severity = severity_for(self.potential_recovery, self.classification)

    @property
    def fingerprint(self) -> str:
        raw = "|".join([self.rule_id, self.invoice.id if self.invoice else "", self.line.id if self.line else "",
                        self.key])
        return hashlib.sha256(raw.encode()).hexdigest()[:40]


def severity_for(recovery: Decimal, classification: Classification) -> Severity:
    if recovery >= SEVERITY_HIGH_FROM:
        return Severity.HIGH
    if recovery >= SEVERITY_MEDIUM_FROM:
        return Severity.MEDIUM
    if recovery > ZERO:
        return Severity.LOW
    return Severity.INFO if classification == Classification.ANOMALY else Severity.LOW


@dataclass(frozen=True)
class Detector:
    rule_id: str
    version: str
    description: str
    run: Callable[[AnalysisContext], list[Finding]]


# ---------------------------------------------------------------- evidence helpers


def invoice_quality(invoice: Invoice, line: InvoiceLine | None = None) -> EvidenceQuality:
    """Verified or structured sources are explicit evidence; unverified PDF parsing is not."""
    if invoice.is_verified:
        return EvidenceQuality.EXPLICIT
    if line is not None and line.extraction_method in STRUCTURED_METHODS:
        return EvidenceQuality.EXPLICIT
    if line is None and invoice.lines and all(li.extraction_method in STRUCTURED_METHODS for li in invoice.lines):
        return EvidenceQuality.EXPLICIT
    return EvidenceQuality.EXTRACTED


def invoice_ref(invoice: Invoice) -> str:
    period = f", periode {invoice.period}" if invoice.period else ""
    return f"factuur {invoice.label}{period}"


def line_evidence(line: InvoiceLine, label: str | None = None) -> dict:
    inv = line.invoice
    loc = f"pagina {line.source_page}" if line.source_page else (f"rij {line.source_row}" if line.source_row else "")
    return {
        "kind": "invoice_line",
        "label": label or f"Factuurregel '{line.description}' ({loc})",
        "document_id": inv.document_id,
        "invoice_id": inv.id,
        "invoice_number": inv.invoice_number,
        "line_id": line.id,
        "page": line.source_page,
        "row": line.source_row,
        "bbox": line.source_bbox,
        "source_text": line.source_text,
    }


def invoice_field_evidence(ctx: AnalysisContext, invoice: Invoice, field_name: str, label: str,
                           value: object) -> dict:
    prov = ctx.provenance.get((invoice.id, field_name))
    return {
        "kind": "invoice_field",
        "label": label,
        "field": field_name,
        "value": _plain(value),
        "document_id": invoice.document_id,
        "invoice_id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "page": prov.page if prov else None,
        "row": prov.row_number if prov else invoice.source_row,
        "bbox": prov.bbox if prov else None,
        "source_text": prov.raw_text if prov else None,
    }


def validity(valid_from: date, valid_to: date | None) -> str:
    return f"vanaf {valid_from:%d-%m-%Y}" if valid_to is None else f"{valid_from:%d-%m-%Y} t/m {valid_to:%d-%m-%Y}"


def contract_price_evidence(cp: ContractPrice) -> dict:
    c = cp.contract
    return {
        "kind": "contract_price",
        "label": (f"Contract {c.contract_reference or c.supplier}: {cp.category.value} "
                  f"€ {format_price(cp.price)}/{cp.unit} geldig {validity(cp.valid_from, cp.valid_to)}"),
        "contract_id": c.id,
        "contract_price_id": cp.id,
        "document_id": c.document_id,
        "page": cp.source_page,
        "source_text": cp.source_text,
        "verified": c.verified_at is not None,
    }


def reading_evidence(r: MeterReading) -> dict:
    loc = f"pagina {r.source_page}" if r.source_page else (f"rij {r.source_row}" if r.source_row else r.source or "")
    return {
        "kind": "meter_reading",
        "label": (f"Meterstand {r.register} {r.reading_date:%d-%m-%Y}: {format_decimal_nl(r.reading)} {r.unit} "
                  f"({r.reading_type.value.lower()}, bron {r.source or '?'} {loc})"),
        "reading_id": r.id,
        "document_id": r.document_id,
        "invoice_id": r.invoice_id,
        "page": r.source_page,
        "row": r.source_row,
        "source_text": r.source_text,
    }


def rate_evidence(rate: ReferenceRate) -> dict:
    return {
        "kind": "reference_rate",
        "label": f"Referentietarief {rate.kind.value} {format_decimal_nl(rate.rate)} ({rate.unit}), "
                 f"geldig vanaf {rate.valid_from:%d-%m-%Y}. Bron: {rate.source_reference}",
        "reference_rate_id": rate.id,
        "source_text": rate.source_reference,
    }


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def eur(v: Decimal | None) -> str:
    return format_eur(v)


def charge_sign(invoice: Invoice) -> int:
    """+1 when a positive difference means the client paid too much.

    Credit notes that print credited amounts as positive numbers invert the meaning.
    """
    if invoice.invoice_type == InvoiceType.CREDIT_NOTE and (invoice.total_incl_vat or ZERO) > ZERO:
        return -1
    return 1
