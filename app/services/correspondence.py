"""Dutch supplier correspondence from case data. Deterministic templates; factual, non-accusatory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.ai.provider import safe_rewrite
from app.domain.enums import Confidence
from app.domain.money import format_eur
from app.models import Anomaly, Client, Invoice, RecoveryCase
from app.models.base import utcnow
from app.services.recovery import conservative_total

RESPONSE_DAYS = 30


@dataclass
class ClaimItem:
    anomaly: Anomaly
    invoice: Invoice | None

    @property
    def firmly_established(self) -> bool:
        """Only reviewer-confirmed, HIGH-confidence, non-tax findings are phrased as established."""
        a = self.anomaly
        return a.is_confirmed and a.confidence == Confidence.HIGH and a.category != "tax"


@dataclass
class Letter:
    subject: str
    body: str
    used_ai: bool = False


def build_items(case: RecoveryCase, invoices: dict[str, Invoice]) -> list[ClaimItem]:
    items = [ClaimItem(a, invoices.get(a.invoice_id)) for a in case.anomalies]
    return sorted(items, key=lambda i: (i.invoice.billing_period_start if i.invoice and i.invoice.billing_period_start
                                        else utcnow().date(), i.anomaly.title))


def generate_letter(case: RecoveryCase, client: Client, items: list[ClaimItem], operator_name: str) -> Letter:
    numbers = sorted({i.invoice.invoice_number for i in items if i.invoice and i.invoice.invoice_number})
    if len(numbers) == 1:
        subject = f"Verzoek tot correctie factuur {numbers[0]}"
    else:
        subject = f"Verzoek tot correctie facturen {', '.join(numbers[:5])}{' e.a.' if len(numbers) > 5 else ''}"
    subject += f" — {client.company_name} (ref. {case.reference})"
    total = conservative_total([i.anomaly for i in items])
    deadline = (utcnow() + timedelta(days=RESPONSE_DAYS)).date()
    kvk = f" (KvK {client.kvk_number})" if client.kvk_number else ""

    parts = [
        "Geachte heer/mevrouw,",
        "",
        f"Namens {client.company_name}{kvk} hebben wij de energiefacturen van {case.supplier} gecontroleerd. "
        "Bij deze controle zijn wij op de hieronder beschreven punten gestuit. Wij verzoeken u deze te "
        "beoordelen en, waar van toepassing, te corrigeren.",
        "",
    ]
    for n, item in enumerate(items, start=1):
        a, inv = item.anomaly, item.invoice
        opening = ("Na controle stellen wij vast dat" if item.firmly_established
                   else "Wij constateren een mogelijke discrepantie:")
        header = f"{n}. {a.title}"
        details = []
        if inv is not None:
            details.append(f"   Factuurnummer: {inv.invoice_number or 'onbekend'}"
                           + (f", factuurdatum {inv.invoice_date:%d-%m-%Y}" if inv.invoice_date else ""))
            if inv.period:
                details.append(f"   Periode: {inv.period}")
            if inv.ean:
                details.append(f"   EAN: {inv.ean}")
        text = _lower_first(a.description) if item.firmly_established else a.description
        parts += [header, *details, f"   {opening} {text}"]
        if a.calculation:
            parts.append("   Berekening:")
            parts += [f"   - {step}" for step in a.calculation]
        parts += [f"   Gevraagde correctie: {format_eur(a.potential_recovery)} (excl. btw)", ""]
    parts += [
        f"Het totaal van de gevraagde correcties bedraagt {format_eur(total)} excl. btw. Overlappende punten zijn "
        "hierin niet dubbel meegeteld.",
        "",
        "Wij verzoeken u vriendelijk:",
        "- de genoemde facturen te controleren;",
        "- het te veel gefactureerde bedrag te crediteren en aan onze opdrachtgever terug te betalen of te "
        "verrekenen;",
        f"- ons uiterlijk {deadline:%d-%m-%Y} schriftelijk te informeren over uw bevindingen.",
        "",
        "Mocht u tot een andere conclusie komen, dan ontvangen wij graag uw onderbouwing, zodat wij deze met "
        "onze opdrachtgever kunnen bespreken.",
        "",
    ]
    if client.consent_given:
        parts += [f"Een machtiging van {client.company_name} om namens haar op te treden is bijgevoegd.", ""]
    parts += ["Bijlagen:", "- Specificatie van de gevraagde correcties",
              "- Kopieën van de betreffende facturen", "", "Met vriendelijke groet,", "", operator_name,
              f"namens {client.company_name}"]
    body = "\n".join(parts)
    body, used_ai = safe_rewrite(client, body, "Verbeter de leesbaarheid; wijzig geen feiten of getallen.")
    return Letter(subject=subject, body=body, used_ai=used_ai)


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text
