"""Presentation helpers: finding summaries, document overlays, dashboard series and SVG charts.

Charts are rendered server-side as SVG (no chart library, CSP-safe). Every chart answers one question;
values carry a <title> tooltip and the underlying numbers are also available as text in the page.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Classification, InvoiceType, LineCategory, ReviewStatus
from app.domain.money import ZERO, format_decimal_nl, format_eur
from app.domain.units import convert_quantity
from app.models import Anomaly, Document, Invoice
from app.services.metrics import POTENTIAL_STATUSES, Summary
from app.services.recovery import conservative_total

MONTHS = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
CATEGORY_NL = {
    "arithmetic": "Rekenfouten", "price": "Tarieven", "fixed_charge": "Vaste kosten", "period": "Factuurperiodes",
    "duplicate": "Dubbele facturering", "consumption": "Verbruik en meterstanden", "credit": "Creditnota's",
    "tax": "Belastingen", "contract": "Contractperiode",
}
LINE_CATEGORY_NL = {
    "ELECTRICITY_NORMAL": "Levering normaal", "ELECTRICITY_LOW": "Levering dal", "ELECTRICITY_SINGLE": "Levering enkel",
    "ELECTRICITY_FEED_IN": "Teruglevering", "GAS_SUPPLY": "Levering gas", "FIXED_SUPPLY_FEE": "Vaste leveringskosten",
    "NETWORK_FIXED": "Netbeheer vast", "NETWORK_CAPACITY": "Capaciteitstarief", "NETWORK_TRANSPORT": "Transport",
    "METER_RENTAL": "Meterhuur", "ENERGY_TAX_ELECTRICITY": "Energiebelasting elektriciteit",
    "ENERGY_TAX_GAS": "Energiebelasting gas", "TAX_REDUCTION": "Vermindering energiebelasting",
    "SURCHARGE": "Toeslag", "DISCOUNT": "Korting", "CORRECTION": "Correctie", "OTHER": "Overig",
}
UNITS_DISPLAY = {"month": "maand", "day": "dag", "year": "jaar", "m3": "m³", "piece": "stuk"}
UNIT_NL = {"EUR": "", "kWh": "kWh", "m3": "m³", "dagen": "dagen"}
_ELEC = {LineCategory.ELECTRICITY_NORMAL, LineCategory.ELECTRICITY_LOW, LineCategory.ELECTRICITY_SINGLE}


# ---------------------------------------------------------------- findings


def fmt_value(v: Decimal | None, unit: str) -> str:
    if v is None:
        return "—"
    if unit == "EUR":
        return format_eur(v)
    unit_txt = UNIT_NL.get(unit, unit)
    places = 0 if unit in ("kWh", "m3", "dagen") and v == v.to_integral_value() else None
    return f"{format_decimal_nl(v, places)} {unit_txt}".strip()


@dataclass
class FindingView:
    kind: str
    kind_class: str
    state_class: str
    status_label: str
    status_class: str
    sentence: str
    location: str | None
    category: str


def finding_view(a: Anomaly) -> FindingView:
    from app.services.review import REVIEW_LABELS_NL

    anomaly = a.classification == Classification.ANOMALY
    status_class = {
        ReviewStatus.CONFIRMED: "positive", ReviewStatus.REJECTED: "quiet", ReviewStatus.DUPLICATE: "quiet",
        ReviewStatus.RESOLVED: "quiet", ReviewStatus.INVESTIGATING: "brand", ReviewStatus.INFO_REQUESTED: "brand",
    }.get(a.review_status, "review")
    state = ("is-confirmed" if a.review_status == ReviewStatus.CONFIRMED else
             "is-closed" if a.review_status in (ReviewStatus.REJECTED, ReviewStatus.DUPLICATE, ReviewStatus.RESOLVED)
             else "is-anomaly" if anomaly else "")
    return FindingView(
        kind="Afwijking" if anomaly else "Mogelijke discrepantie",
        kind_class="neutral" if anomaly else "review",
        state_class=state,
        status_label=REVIEW_LABELS_NL[a.review_status],
        status_class=status_class,
        sentence=summary_sentence(a),
        location=evidence_location(a),
        category=CATEGORY_NL.get(a.category, a.category),
    )


def summary_sentence(a: Anomaly) -> str:
    """One sentence a reviewer can grasp in seconds. Numbers come from the finding, never invented."""
    act, exp, unit = a.actual_value, a.expected_value, a.unit
    if act is not None and exp is not None and exp != 0 and unit in ("EUR", "kWh", "m3"):
        pct = (act - exp) / abs(exp) * 100
        direction = "hoger" if pct > 0 else "lager"
        pct_txt = format_decimal_nl(abs(pct).quantize(Decimal("0.1")))
        subject = {"consumption": "Het gefactureerde verbruik is", "price": "Het gefactureerde bedrag is",
                   "fixed_charge": "De gefactureerde vaste kosten zijn", "tax": "Het berekende belastingbedrag is"
                   }.get(a.category, "Het gefactureerde bedrag is")
        basis = {"consumption": "de meterstanden aangeven", "price": "het contracttarief oplevert",
                 "fixed_charge": "voor deze periode verwacht", "tax": "de referentietarieven opleveren",
                 "arithmetic": "de eigen berekening op de factuur oplevert"}.get(a.category, "verwacht")
        return (f"{subject} {pct_txt}% {direction} dan {basis}: "
                f"{fmt_value(act, unit)} in plaats van {fmt_value(exp, unit)}.")
    first = a.description.split(". ")[0].rstrip(".")
    return first + "."


def evidence_location(a: Anomaly) -> str | None:
    number = a.invoice.invoice_number if a.invoice is not None else None
    for key, word in (("page", "pagina"), ("row", "rij")):
        for e in a.evidence or []:
            if e.get(key):
                nr = e.get("invoice_number") or number
                return f"Factuur {nr} · {word} {e[key]}" if nr else f"{word.capitalize()} {e[key]}"
    return None


# ---------------------------------------------------------------- document overlays


def ensure_page_sizes(db: Session, doc: Document) -> list[list[float]] | None:
    if doc.mime_type != "application/pdf":
        return None
    if doc.page_sizes:
        return doc.page_sizes
    from app.extraction.pdf_text import read_pdf
    from app.ingestion.storage import get_store

    try:
        pdf = read_pdf(get_store().get(doc.storage_key))
    except Exception:
        return None
    doc.page_sizes = [list(s) for s in pdf.page_sizes]
    db.commit()
    return doc.page_sizes


@dataclass
class Box:
    id: str
    x: float
    y: float
    w: float
    h: float
    cls: str
    label: str


def box_for(bbox: list[float] | None, box_id: str, cls: str = "hl", label: str = "") -> Box | None:
    if not bbox or len(bbox) != 4:
        return None
    x0, top, x1, bottom = bbox
    pad = 3
    return Box(box_id, x0 - pad, top - pad, (x1 - x0) + 2 * pad + 6, (bottom - top) + 2 * pad, cls, label)


# ---------------------------------------------------------------- dashboard series


@dataclass
class MonthPoint:
    key: tuple[int, int]
    label: str
    invoice_value: Decimal
    kwh: Decimal
    flagged: Decimal  # potential recovery found on invoices of this month


def monthly_series(db: Session, client_id: str | None, months: int = 12) -> list[MonthPoint]:
    q = select(Invoice).where(Invoice.invoice_type == InvoiceType.INVOICE)
    if client_id:
        q = q.where(Invoice.client_id == client_id)
    invoices = [i for i in db.scalars(q).all() if i.billing_period_start]
    aq = select(Anomaly).where(Anomaly.is_stale.is_(False), Anomaly.review_status.in_(POTENTIAL_STATUSES))
    if client_id:
        aq = aq.where(Anomaly.client_id == client_id)
    by_invoice: dict[str, list[Anomaly]] = defaultdict(list)
    for a in db.scalars(aq).all():
        if a.invoice_id:
            by_invoice[a.invoice_id].append(a)
    buckets: dict[tuple[int, int], dict] = defaultdict(lambda: {"v": ZERO, "k": ZERO, "f": []})
    seen: set[tuple] = set()
    for inv in invoices:
        dedupe = ((inv.supplier or "").lower(), inv.invoice_number, inv.total_incl_vat)
        if inv.invoice_number and dedupe in seen:
            continue
        seen.add(dedupe)
        key = (inv.billing_period_start.year, inv.billing_period_start.month)
        b = buckets[key]
        b["v"] += inv.total_incl_vat or ZERO
        for li in inv.lines:
            if li.category in _ELEC and li.quantity is not None:
                q = convert_quantity(li.quantity, li.unit, "kWh")
                if q is not None:
                    b["k"] += q
        b["f"].extend(by_invoice.get(inv.id, []))
    keys = sorted(buckets)[-months:]
    return [MonthPoint(k, f"{MONTHS[k[1] - 1]} {str(k[0])[2:]}", buckets[k]["v"], buckets[k]["k"],
                       conservative_total(buckets[k]["f"])) for k in keys]


@dataclass
class Stage:
    name: str
    amount: Decimal
    cls: str
    note: str


def pipeline_stages(s: Summary) -> list[Stage]:
    return [
        Stage("Mogelijk", s.potential_discrepancies, "review", "nog te verifiëren"),
        Stage("Bevestigd", s.confirmed_discrepancies, "brand", "na beoordeling"),
        Stage("Ingediend", s.claimed_amount, "brand", "bij leverancier"),
        Stage("Toegekend", s.supplier_approved, "brand", "door leverancier"),
        Stage("Teruggevorderd", s.recovered, "positive", "ontvangen"),
    ]


def category_distribution(anomalies: list[Anomaly]) -> list[tuple[str, int, Decimal]]:
    groups: dict[str, list[Anomaly]] = defaultdict(list)
    for a in anomalies:
        if a.review_status in POTENTIAL_STATUSES:
            groups[a.category].append(a)
    rows = [(CATEGORY_NL.get(k, k), len(v), conservative_total(v)) for k, v in groups.items()]
    return sorted(rows, key=lambda r: (-r[2], -r[1]))


# ---------------------------------------------------------------- SVG charts


def nice_ticks(v: float, max_steps: int = 5) -> list[float]:
    """Round axis ticks (0, 100, 200 …) covering v with at most max_steps intervals."""
    if v <= 0:
        return [0.0, 1.0]
    raw = v / max_steps
    exp = 10 ** math.floor(math.log10(raw))
    step = next(m * exp for m in (1, 2, 2.5, 5, 10) if m * exp >= raw)
    n = math.ceil(v / step)
    return [step * i for i in range(n + 1)]


def _short_eur(v: float) -> str:
    if v >= 1_000_000:
        return f"€ {v / 1_000_000:.1f} mln".replace(".", ",")
    if v >= 1000:
        return f"€ {v / 1000:.0f}k" if v >= 10_000 else f"€ {v / 1000:.1f}k".replace(".", ",")
    return f"€ {v:.0f}"


def bar_chart(points: list[MonthPoint]) -> Markup:
    """Invoice value per month; months with potential discrepancies in the review color."""
    W, H, L, R, T, B = 800, 220, 56, 8, 12, 26
    ticks = nice_ticks(float(max((p.invoice_value for p in points), default=ZERO)), 4)
    top = ticks[-1]
    pw, ph = W - L - R, H - T - B
    slot = pw / max(len(points), 1)
    bw = min(24, slot * 0.56)
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Factuurwaarde per maand">']
    for v in ticks:
        y = T + ph - ph * v / top
        out.append(f'<line class="grid-line" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end">{_short_eur(v)}</text>')
    for i, p in enumerate(points):
        v = float(p.invoice_value)
        h = max(0.0, ph * v / top)
        x = L + slot * i + (slot - bw) / 2
        y = T + ph - h
        r = min(4, h / 2, bw / 2)
        flag = p.flagged > 0
        path = (f"M{x:.1f},{T + ph:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bw - r:.1f} "
                f"Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{T + ph:.1f} Z") if h > 0 else ""
        tip = f"{p.label}: {format_eur(p.invoice_value)} gefactureerd" + (
            f" · mogelijke discrepantie {format_eur(p.flagged)}" if flag else "")
        out.append(f'<g class="col"><title>{escape(tip)}</title>'
                   f'<rect class="bar-hit" x="{L + slot * i:.1f}" y="{T}" width="{slot:.1f}" height="{ph}"/>'
                   + (f'<path class="bar{" flag" if flag else ""}" d="{path}"/>' if path else "")
                   + f'<text x="{x + bw / 2:.1f}" y="{H - 8}" text-anchor="middle">{escape(p.label)}</text></g>')
    out.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}"/></svg>')
    return Markup("".join(out))


def line_chart(points: list[MonthPoint]) -> Markup:
    """Electricity consumption per month; months with findings marked."""
    pts = [p for p in points if p.kwh > 0]
    W, H, L, R, T, B = 800, 200, 56, 16, 16, 26
    if len(pts) < 2:
        return Markup("")
    ticks = nice_ticks(float(max(p.kwh for p in pts)), 4)
    top = ticks[-1]
    pw, ph = W - L - R, H - T - B
    step = pw / (len(pts) - 1)
    xy = [(L + step * i, T + ph - ph * float(p.kwh) / top) for i, p in enumerate(pts)]
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Elektriciteitsverbruik per maand">']
    for v in ticks:
        y = T + ph - ph * v / top
        out.append(f'<line class="grid-line" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" text-anchor="end">{format_decimal_nl(Decimal(round(v)))}</text>')
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    area = (f"M{xy[0][0]:.1f},{T + ph} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in xy)
            + f" L{xy[-1][0]:.1f},{T + ph} Z")
    out.append(f'<path class="area" d="{area}"/><polyline class="line" points="{line}"/>')
    for (x, y), p in zip(xy, pts, strict=True):
        flag = p.flagged > 0
        tip = f"{p.label}: {format_decimal_nl(p.kwh.quantize(Decimal(1)))} kWh" + (" · bevinding" if flag else "")
        out.append(f'<g><title>{escape(tip)}</title><circle class="dot{" flag" if flag else ""}" cx="{x:.1f}" '
                   f'cy="{y:.1f}" r="{5 if flag else 4}"/>'
                   f'<text x="{x:.1f}" y="{H - 8}" text-anchor="middle">{escape(p.label)}</text></g>')
    last_x, last_y = xy[-1]
    out.append(f'<text class="val" x="{last_x:.1f}" y="{last_y - 10:.1f}" text-anchor="end">'
               f'{format_decimal_nl(pts[-1].kwh.quantize(Decimal(1)))} kWh</text>')
    out.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}"/></svg>')
    return Markup("".join(out))


def meter_bar(value: Decimal, maximum: Decimal, cls: str = "") -> Markup:
    pct = 0.0 if not maximum else max(0.0, min(100.0, float(value) / float(maximum) * 100))
    fill = f'<rect class="fill {cls}" x="0" y="0" width="{pct:.2f}%" height="10" rx="3"/>' if pct > 0 else ""
    return Markup(f'<svg aria-hidden="true"><rect class="track" x="0" y="0" width="100%" height="10" rx="3"/>'
                  f'{fill}</svg>')


def month_label(d: date | None) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}" if d else "—"
