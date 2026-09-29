"""Audit trail. Stores ids and action names only — never amounts, document content, or free text from users."""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def audit(db: Session, action: str, *, user: User | None = None, client_id: str | None = None,
          object_type: str | None = None, object_id: str | None = None, ip: str | None = None,
          details: dict | None = None) -> None:
    db.add(AuditLog(
        user_id=user.id if user else None,
        client_id=client_id if client_id is not None else (user.client_id if user else None),
        action=action, object_type=object_type, object_id=object_id, ip_address=ip, details=details,
    ))


_AMOUNT = re.compile(r"€\s?-?[\d.,]+|\b\d{1,3}(?:\.\d{3})+,\d{2}\b|\b\d+,\d{2}\b")
_LONG_DIGITS = re.compile(r"\b\d{9,}\b")  # EAN, IBAN digits, KvK+ etc.
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def scrub(text: str) -> str:
    text = _IBAN.sub("[IBAN]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _AMOUNT.sub("[BEDRAG]", text)
    return _LONG_DIGITS.sub("[NUMMER]", text)


class SensitiveDataFilter(logging.Filter):
    """Strip amounts, long digit runs (EAN/IBAN) and e-mail addresses from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = scrub(msg)
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if not any(isinstance(f, SensitiveDataFilter) for h in root.handlers for f in h.filters):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(SensitiveDataFilter())
        root.addHandler(handler)
    root.setLevel(level)
