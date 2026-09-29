"""Document-type classification from text (deterministic keyword scoring)."""

import re

from app.domain.enums import DocumentType

_INVOICE = re.compile(r"factuurnummer|factuurdatum|notanummer|jaarafrekening|termijnfactuur|eindafrekening|"
                      r"te betalen|factuur", re.I)
_ENERGY = re.compile(r"kwh|\bm3\b|m³|energiebelasting|ean|aansluiting|levering", re.I)
_CREDIT = re.compile(r"creditnota|creditfactuur|credit\s*nota", re.I)
_CONTRACT = re.compile(r"overeenkomst|contract|leveringsvoorwaarden|algemene voorwaarden|looptijd|aanbieding", re.I)


def classify_text(text: str) -> tuple[DocumentType, float]:
    head = text[:6000]
    if _CREDIT.search(head) and _ENERGY.search(text):
        return DocumentType.CREDIT_NOTE, 0.9
    invoice_hits = len(_INVOICE.findall(head))
    contract_hits = len(_CONTRACT.findall(head))
    energy = bool(_ENERGY.search(text))
    if invoice_hits >= 2 and energy and invoice_hits >= contract_hits:
        return DocumentType.INVOICE, 0.9
    if contract_hits >= 2 and contract_hits > invoice_hits:
        return DocumentType.CONTRACT, 0.75
    if invoice_hits >= 1 and energy:
        return DocumentType.INVOICE, 0.6
    return DocumentType.OTHER, 0.4
