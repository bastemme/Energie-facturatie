"""Enumerations shared across the domain. Values are stored as strings in the DB."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    REVIEWER = "REVIEWER"
    CLIENT = "CLIENT"


class DocumentType(StrEnum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    INVOICE_TABLE = "INVOICE_TABLE"  # CSV/XLSX with invoice lines
    METER_DATA = "METER_DATA"
    CONTRACT = "CONTRACT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSED = "PROCESSED"
    NEEDS_REVIEW = "NEEDS_REVIEW"  # extracted, but low confidence or failed validation
    NEEDS_OCR = "NEEDS_OCR"  # no text layer: manual entry until OCR exists
    FAILED = "FAILED"
    STORED = "STORED"  # stored as supporting document, no extraction


class InvoiceType(StrEnum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"


class Commodity(StrEnum):
    ELECTRICITY = "ELECTRICITY"
    GAS = "GAS"
    MIXED = "MIXED"
    UNKNOWN = "UNKNOWN"


class LineCategory(StrEnum):
    ELECTRICITY_NORMAL = "ELECTRICITY_NORMAL"  # normaaltarief / piek
    ELECTRICITY_LOW = "ELECTRICITY_LOW"  # daltarief
    ELECTRICITY_SINGLE = "ELECTRICITY_SINGLE"  # enkeltarief
    ELECTRICITY_FEED_IN = "ELECTRICITY_FEED_IN"  # teruglevering
    GAS_SUPPLY = "GAS_SUPPLY"
    FIXED_SUPPLY_FEE = "FIXED_SUPPLY_FEE"  # vaste leveringskosten
    NETWORK_FIXED = "NETWORK_FIXED"  # vastrecht netbeheer
    NETWORK_CAPACITY = "NETWORK_CAPACITY"  # capaciteitstarief
    NETWORK_TRANSPORT = "NETWORK_TRANSPORT"  # transportdienst / kWmax
    METER_RENTAL = "METER_RENTAL"  # meterhuur / meetdienst
    ENERGY_TAX_ELECTRICITY = "ENERGY_TAX_ELECTRICITY"
    ENERGY_TAX_GAS = "ENERGY_TAX_GAS"
    TAX_REDUCTION = "TAX_REDUCTION"  # vermindering energiebelasting
    SURCHARGE = "SURCHARGE"  # toeslagen, GVO, profielkosten
    DISCOUNT = "DISCOUNT"
    CORRECTION = "CORRECTION"
    OTHER = "OTHER"

    @property
    def is_consumption(self) -> bool:
        return self in CONSUMPTION_CATEGORIES

    @property
    def is_fixed_charge(self) -> bool:
        return self in FIXED_CHARGE_CATEGORIES

    @property
    def is_energy_tax(self) -> bool:
        return self in (LineCategory.ENERGY_TAX_ELECTRICITY, LineCategory.ENERGY_TAX_GAS)


CONSUMPTION_CATEGORIES = frozenset(
    {
        LineCategory.ELECTRICITY_NORMAL,
        LineCategory.ELECTRICITY_LOW,
        LineCategory.ELECTRICITY_SINGLE,
        LineCategory.GAS_SUPPLY,
    }
)
FIXED_CHARGE_CATEGORIES = frozenset(
    {
        LineCategory.FIXED_SUPPLY_FEE,
        LineCategory.NETWORK_FIXED,
        LineCategory.NETWORK_CAPACITY,
        LineCategory.METER_RENTAL,
    }
)


class ReadingType(StrEnum):
    ACTUAL = "ACTUAL"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


class ExtractionMethod(StrEnum):
    REGEX = "REGEX"
    PDF_TABLE = "PDF_TABLE"
    CSV = "CSV"
    XLSX = "XLSX"
    MANUAL = "MANUAL"
    DERIVED = "DERIVED"  # computed deterministically from other extracted values
    LLM_VERIFIED = "LLM_VERIFIED"  # LLM proposed, literal match on page verified


class Classification(StrEnum):
    ANOMALY = "ANOMALY"  # unusual, not proof
    POTENTIAL_ERROR = "POTENTIAL_ERROR"  # deterministic mismatch, requires verification


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def rank(self) -> int:
        return {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[self.value]


class ReviewStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    INFO_REQUESTED = "INFO_REQUESTED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"
    RESOLVED = "RESOLVED"


class CaseStatus(StrEnum):
    DETECTED = "DETECTED"
    REVIEW = "REVIEW"
    VERIFIED = "VERIFIED"
    CLIENT_APPROVAL = "CLIENT_APPROVAL"
    SUBMITTED = "SUBMITTED"
    SUPPLIER_REVIEW = "SUPPLIER_REVIEW"
    NEGOTIATION = "NEGOTIATION"
    APPROVED = "APPROVED"
    RECOVERED = "RECOVERED"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    DISPUTED = "DISPUTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class RateKind(StrEnum):
    VAT = "VAT"
    ENERGY_TAX_ELECTRICITY = "ENERGY_TAX_ELECTRICITY"
    ENERGY_TAX_GAS = "ENERGY_TAX_GAS"


class LeadStatus(StrEnum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUALIFIED = "QUALIFIED"
    CONVERTED = "CONVERTED"
    DISQUALIFIED = "DISQUALIFIED"
