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
from app.domain.money import format_decimal_nl, format_eur
from app.extraction.parsing import parse_date, parse_decimal
from app.services.cases import STATUS_LABELS_NL
from app.services.review import REVIEW_LABELS_NL
from app.web.security import csrf_token

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


templates.env.filters.update(eur=format_eur, nl=_num, d=_date, pct=_pct)
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
    return templates.TemplateResponse(request, name, context, status_code=status_code)


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
