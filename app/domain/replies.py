"""Classify a reply to an outreach e-mail. Deterministic rules (Dutch and English phrases), so every
classification can be explained: the matched phrases are returned as reasons.

Precedence matters: an unsubscribe request wins over everything, an auto-reply is never read as interest,
and "geen interesse" must not count as "interesse".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.enums import Confidence, ReplyCategory

C = ReplyCategory

RULES: list[tuple[ReplyCategory, tuple[str, ...]]] = [
    (C.UNSUBSCRIBE, (r"\bafmelden\b", r"\bafgemeld\b", r"\buitschrijven\b", r"geen (e-?mails?|berichten|mail) meer",
                     r"verwijder (mij|ons|me)", r"niet meer (mailen|benaderen|contacteren)", r"\bunsubscribe\b",
                     r"remove me", r"stop (met )?mailen")),
    (C.OUT_OF_OFFICE, (r"\bafwezig\b", r"out of office", r"automatisch(e)? (antwoord|bericht)", r"\bautomatic reply\b",
                       r"ben (weer|vanaf) .{0,20}(aanwezig|bereikbaar|terug)", r"met vakantie", r"\bon leave\b",
                       r"niet op kantoor")),
    (C.WRONG_PERSON, (r"niet de (juiste|goede) (persoon|contactpersoon)", r"verkeerde (persoon|contactpersoon)",
                      r"(ga|gaat) (ik )?(hier )?niet over", r"(collega|afdeling) .{0,40}(gaat|over) (hierover|dit)",
                      r"doorgestuurd naar", r"wrong person", r"not the right person", r"werk hier niet meer")),
    (C.MEETING_REQUEST, (r"\bafspraak\b", r"\bbelafspraak\b", r"\bkennismak", r"\b(in )?(de )?agenda\b",
                         r"(even )?bellen\b", r"\bgesprek\b", r"\bmeeting\b", r"\bteams[- ]?(call|meeting)?\b",
                         r"\bschedule a call\b", r"(maandag|dinsdag|woensdag|donderdag|vrijdag)\b.{0,30}"
                         r"(uur|ochtend|middag|om)")),
    (C.NOT_INTERESTED, (r"geen interesse", r"niet (ge)?ïnteresseerd", r"niet geinteresseerd", r"niet interessant",
                        r"nee,? dank", r"hebben (dit|dat) al", r"al (een|iemand) die", r"not interested",
                        r"no thanks", r"geen behoefte")),
    (C.MORE_INFORMATION, (r"meer informatie", r"meer info\b", r"hoe werkt", r"wat kost", r"wat zijn de (kosten|"
                          r"voorwaarden)", r"\bvoorwaarden\b", r"(stuur|mail) .{0,30}(informatie|info|brochure|"
                          r"voorbeeld)", r"\bbrochure\b", r"more information", r"how does (it|this) work")),
    (C.INTERESTED, (r"\binteresse\b", r"\binteressant\b", r"klinkt goed", r"\bgeïnteresseerd\b", r"\bgeinteresseerd\b",
                    r"lijkt me (goed|interessant|zinvol)", r"graag\b", r"\binterested\b", r"sounds good")),
    (C.FOLLOW_UP, (r"\b(later|volgend(e)? (kwartaal|jaar)|later dit jaar)\b", r"na de (zomer|vakantie)",
                   r"over (een|twee|drie|\d+) (week|weken|maand|maanden)", r"neem .{0,15}later contact",
                   r"kom .{0,15}terug", r"begin (volgend )?jaar", r"follow up later")),
]
# Categories that are negations of a later rule: their phrases must not also count as the positive category.
_SUPPRESS = {C.NOT_INTERESTED: {C.INTERESTED}}
# Signals that agree with each other (a meeting request is also interest); they do not lower the confidence.
_COMPATIBLE = {C.MEETING_REQUEST: {C.INTERESTED, C.MORE_INFORMATION}, C.MORE_INFORMATION: {C.INTERESTED},
               C.INTERESTED: set()}


LABELS_NL = {C.INTERESTED: "geïnteresseerd", C.MEETING_REQUEST: "wil een gesprek",
             C.MORE_INFORMATION: "wil meer informatie", C.NOT_INTERESTED: "geen interesse",
             C.WRONG_PERSON: "verkeerde persoon", C.FOLLOW_UP: "later opvolgen", C.UNSUBSCRIBE: "afgemeld",
             C.OUT_OF_OFFICE: "afwezig", C.OTHER: "overig"}


@dataclass
class ReplyClassification:
    category: ReplyCategory
    confidence: Confidence
    reasons: list[str]


def _strip_quoted(text: str) -> str:
    """Only classify what the person wrote, not our quoted original message below it."""
    lines = []
    for line in text.splitlines():
        if line.startswith(">") or re.match(r"^(op .+ schreef|on .+ wrote|-----\s*original message|van:\s)", line,
                                              re.I):
            break
        lines.append(line)
    return "\n".join(lines)


def classify_reply(subject: str, body: str) -> ReplyClassification:
    text = f"{subject}\n{_strip_quoted(body)}".lower()
    matches: dict[ReplyCategory, list[str]] = {}
    for category, patterns in RULES:
        found = [m.group(0) for p in patterns if (m := re.search(p, text))]
        if found:
            matches[category] = found
    for negative, positives in _SUPPRESS.items():
        if negative in matches:
            for pos in positives:
                matches.pop(pos, None)
    for category, _ in RULES:  # precedence order
        if category in matches:
            found = matches[category]
            confidence = Confidence.HIGH if len(found) >= 2 or category in (C.UNSUBSCRIBE, C.OUT_OF_OFFICE) \
                else Confidence.MEDIUM
            conflicting = set(matches) - {category} - _COMPATIBLE.get(category, set())
            if category in (C.MEETING_REQUEST, C.MORE_INFORMATION) and len(found) == 1 and not conflicting and \
                    matches.keys() & _COMPATIBLE[category]:
                confidence = Confidence.HIGH  # e.g. "interessant" + "bellen": both point the same way
            if conflicting and confidence == Confidence.HIGH and category not in (C.UNSUBSCRIBE, C.OUT_OF_OFFICE):
                confidence = Confidence.MEDIUM  # mixed signals: a person should read it
            others = [f"ook signaal: {LABELS_NL[c]}" for c in matches if c != category]
            return ReplyClassification(category, confidence, [f"'{f}'" for f in found] + others)
    return ReplyClassification(C.OTHER, Confidence.LOW, ["geen herkenbare formulering"])
