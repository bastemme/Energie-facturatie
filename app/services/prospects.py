"""Prospects: companies found by research (outbound pipeline)."""

from __future__ import annotations

import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Prospect


def normalize_name(name: str) -> str:
    name = re.sub(r"\b(b\.?v\.?|v\.?o\.?f\.?|n\.?v\.?|holding)\b", "", name.lower())
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def find_duplicate(db: Session, *, source_ref: str | None, domain: str | None, name: str,
                   city: str | None) -> Prospect | None:
    conds = []
    if source_ref:
        conds.append(Prospect.source_ref == source_ref)
    if domain:
        conds.append(Prospect.domain == domain)
    for p in db.scalars(select(Prospect).where(or_(*conds))).all() if conds else []:
        return p
    if city:
        for p in db.scalars(select(Prospect).where(func.lower(Prospect.city) == city.lower())).all():
            if normalize_name(p.company_name) == normalize_name(name):
                return p
    return None


def save_prospect(db: Session, **fields) -> Prospect:
    prospect = Prospect(**fields)
    db.add(prospect)
    db.flush()
    return prospect
