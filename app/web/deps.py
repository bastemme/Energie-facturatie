"""Template rendering helpers shared by all routes."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.domain.enums import CaseStatus, Confidence, ReviewStatus
from app.domain.money import format_decimal_nl, format_eur, format_price
from app.extraction.parsing import parse_date, parse_decimal
from app.services.cases import STATUS_LABELS_NL
from app.services.review import REVIEW_LABELS_NL
from app.web.security import csrf_token
from app.web.view import LINE_CATEGORY_NL, UNITS_DISPLAY

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _date(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, datetime):
        return v.strftime("%d-%m-%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d-%m-%Y")
    return str(v)


def _num(v, places=None) -> str:
    if v is None:
        return "—"
    return format_decimal_nl(Decimal(v), places)


def _pct(v) -> str:
    return "—" if v is None else f"{v * 100:.1f}%".replace(".", ",")


def _cat(v) -> str:
    return LINE_CATEGORY_NL.get(getattr(v, "value", v), str(v))


def _unit(v) -> str:
    return UNITS_DISPLAY.get(v, v) if v else ""


def _price(v) -> str:
    return "—" if v is None else "€ " + format_price(Decimal(v))


def _ago(v) -> str:
    if v is None:
        return "—"
    from app.models.base import utcnow

    now = utcnow()
    if v.tzinfo is None:
        v = v.replace(tzinfo=now.tzinfo)
    secs = int((now - v).total_seconds())
    if secs < 45:
        return "zojuist"
    if secs < 3600:
        return f"{max(1, secs // 60)} min geleden"
    if secs < 86400:
        return f"{secs // 3600} uur geleden"
    return v.strftime("%d-%m-%Y")


def _duration(ms) -> str:
    if ms is None:
        return "—"
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s".replace(".", ",")


def _pretty(v) -> str:
    import json

    return json.dumps(v, indent=2, ensure_ascii=False, default=str)


def _time(v) -> str:
    return v.strftime("%H:%M:%S") if v else ""


templates.env.filters.update(eur=format_eur, nl=_num, d=_date, pct=_pct, cat=_cat, unit=_unit, price=_price,
                             ago=_ago, duration=_duration, pretty=_pretty, hms=_time)
from app.web.icons import LOGO, icon  # noqa: E402
from app.web.view import finding_view, fmt_value, meter_bar  # noqa: E402

templates.env.globals.update(icon=icon, LOGO=LOGO, finding_view=finding_view, fmt_value=fmt_value,
                             meter_bar=meter_bar)
templates.env.globals.update(
    CASE_LABELS=STATUS_LABELS_NL, REVIEW_LABELS=REVIEW_LABELS_NL, ReviewStatus=ReviewStatus, CaseStatus=CaseStatus,
    Confidence=Confidence,
)


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", []).append({"kind": kind, "message": message})


def render(request: Request, name: str, status_code: int = 200, **context):
    settings = get_settings()
    messages = request.session.pop("flash", [])
    context.update(request=request, csrf=csrf_token(request), messages=messages,
                   operator_name=settings.operator_name, operator_email=settings.operator_email)
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("path", request.url.path)
    user = context.get("user")
    if user is not None and user.is_staff and "queue_count" not in context:
        context["queue_count"] = _queue_count()
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _queue_count() -> int:
    from sqlalchemy import func, select

    from app.db import session_factory
    from app.models import Anomaly

    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(Anomaly).where(
            Anomaly.is_stale.is_(False),
            Anomaly.review_status.in_([ReviewStatus.OPEN, ReviewStatus.INVESTIGATING, ReviewStatus.INFO_REQUESTED]),
        )) or 0


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def form_decimal(value: str | None) -> Decimal | None:
    if value is None or not str(value).strip():
        return None
    parsed = parse_decimal(str(value))
    if parsed is None:
        raise ValueError(f"Ongeldig getal: {value}")
    return parsed.value


def form_date(value: str | None) -> date | None:
    if value is None or not str(value).strip():
        return None
    d = parse_date(str(value))
    if d is None:
        raise ValueError(f"Ongeldige datum: {value}")
    return d


def attachment(filename: str) -> dict[str, str]:
    """Content-Disposition that is safe for any filename (RFC 6266/5987) plus nosniff."""
    ascii_name = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\' else "_" for ch in filename) or "download"
    return {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff"}


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
