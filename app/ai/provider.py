"""AI provider boundary.

AI is OFF by default. It may only be used when globally enabled AND the client has consented
(`ai_processing_allowed`), and never as a source of numbers: any AI output that changes a numeric
token is rejected (see `numbers_preserved`).
"""

from __future__ import annotations

import re
from typing import Protocol

from app.config import get_settings
from app.models import Client

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_preserved(original: str, rewritten: str) -> bool:
    """True when the rewritten text contains exactly the same numeric tokens (as a multiset)."""
    return sorted(_NUMBER.findall(original)) == sorted(_NUMBER.findall(rewritten))


class AIProvider(Protocol):
    name: str

    def rewrite(self, text: str, instruction: str) -> str: ...


class NullProvider:
    name = "none"

    def rewrite(self, text: str, instruction: str) -> str:
        return text


def ai_allowed(client: Client | None) -> bool:
    s = get_settings()
    return bool(s.ai_enabled and s.ai_provider != "none" and client is not None and client.ai_processing_allowed)


def get_provider(client: Client | None) -> AIProvider:
    if not ai_allowed(client):
        return NullProvider()
    # Real providers are added here once a processor agreement covers them (see docs, AI boundaries).
    return NullProvider()


def safe_rewrite(client: Client | None, text: str, instruction: str) -> tuple[str, bool]:
    """Returns (text, used_ai). Falls back to the deterministic text if numbers changed."""
    provider = get_provider(client)
    if isinstance(provider, NullProvider):
        return text, False
    candidate = provider.rewrite(text, instruction)
    if not numbers_preserved(text, candidate):
        return text, False
    return candidate, True
