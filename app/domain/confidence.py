"""Confidence is derived from evidence quality, never from model output."""

from enum import StrEnum

from app.domain.enums import Confidence


class EvidenceQuality(StrEnum):
    EXPLICIT = "EXPLICIT"  # verified document value, structured export, or verified reference/contract row
    EXTRACTED = "EXTRACTED"  # value parsed from a PDF but not yet human-verified
    DERIVED = "DERIVED"  # computed via normalization/inference (category mapping, prorating)
    ESTIMATED = "ESTIMATED"  # based on estimated readings
    STATISTICAL = "STATISTICAL"  # comparison with history / patterns


_QUALITY_CAP = {
    EvidenceQuality.EXPLICIT: Confidence.HIGH,
    EvidenceQuality.EXTRACTED: Confidence.MEDIUM,  # HIGH requires human-verified or structured evidence
    EvidenceQuality.DERIVED: Confidence.MEDIUM,
    EvidenceQuality.ESTIMATED: Confidence.LOW,
    EvidenceQuality.STATISTICAL: Confidence.LOW,
}


def confidence_from_evidence(
    qualities: list[EvidenceQuality],
    *,
    extraction_confidence: float | None = None,
    cap: Confidence | None = None,
) -> Confidence:
    """The weakest link determines confidence.

    - Any statistical/estimated input → LOW.
    - Any derived input → at most MEDIUM.
    - Extraction confidence below 0.9 lowers by one level; below 0.6 → LOW.
    - An explicit cap (e.g. tax rules → MEDIUM) always applies.
    """
    if not qualities:
        return Confidence.LOW
    level = min((_QUALITY_CAP[q] for q in qualities), key=lambda c: c.rank)
    if extraction_confidence is not None:
        if extraction_confidence < 0.6:
            level = Confidence.LOW
        elif extraction_confidence < 0.9 and level == Confidence.HIGH:
            level = Confidence.MEDIUM
    if cap is not None and cap.rank < level.rank:
        level = cap
    return level
