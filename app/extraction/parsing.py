"""Deterministic parsers for Dutch invoice notation (numbers, amounts, dates)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

MONTHS_NL = {
    "januari": 1, "jan": 1, "februari": 2, "feb": 2, "maart": 3, "mrt": 3, "mar": 3,
    "april": 4, "apr": 4, "mei": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "augustus": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10, "okt": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Regex fragments reused by the invoice parser
NUMBER_PATTERN = r"-?\s?\d{1,3}(?:[.\s]\d{3})*(?:,\d+)?-?|-?\d+(?:[.,]\d+)?-?"
AMOUNT_PATTERN = r"-?\s?€?\s?-?\s?\d{1,3}(?:\.\d{3})*,\d{2}-?|-?\s?€?\s?-?\s?\d+,\d{2}-?"
DATE_PATTERN = (
    r"\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s+(?:" + "|".join(sorted(MONTHS_NL, key=len, reverse=True)) + r")\.?\s+\d{4}"
)


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal
    ambiguous: bool = False  # e.g. "1.234" could be 1234 (NL thousands) or 1.234


def parse_decimal(text: str | None, *, locale: str = "nl") -> ParsedNumber | None:
    """Parse '1.234,56', '-12,00', '12,00-', '€ 1.234', '0,12345', '1234.56'.

    Returns None when the text is not a number — never guesses a value.
    """
    if text is None:
        return None
    s = str(text).strip().replace(" ", " ").replace("€", "").replace("EUR", "").strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    if s.endswith("-"):
        negative, s = True, s[:-1].strip()
    if s.startswith("-"):
        negative, s = True, s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.replace(" ", "")
    if not s or not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        return None

    ambiguous = False
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        if s.count(",") > 1:
            if not re.fullmatch(r"\d{1,3}(,\d{3})+", s):
                return None
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif has_dot:
        if s.count(".") > 1:
            if not re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
                return None
            s = s.replace(".", "")
        elif re.fullmatch(r"\d{1,3}\.\d{3}", s):
            ambiguous = True
            if locale == "nl":
                s = s.replace(".", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return ParsedNumber(-value if negative else value, ambiguous)


def parse_date(text: str | None) -> date | None:
    if text is None:
        return None
    if isinstance(text, datetime):
        return text.date()
    if isinstance(text, date):
        return text
    s = str(text).strip().lower().rstrip(".")
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ t].*)?", s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        if y < 100:
            y += 2000
        return _safe_date(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})", s)
    if m and m.group(2) in MONTHS_NL:
        return _safe_date(int(m.group(3)), MONTHS_NL[m.group(2)], int(m.group(1)))
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None
