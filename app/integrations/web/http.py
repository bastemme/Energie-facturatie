"""Outbound HTTP for agents: safe by default.

- only http/https, only public IP addresses (blocks SSRF to localhost, private networks, cloud metadata)
- every redirect hop is re-validated; at most 3 redirects
- hard limits on response size and time; identifying User-Agent
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from app.config import get_settings

MAX_REDIRECTS = 3


class WebAccessError(Exception):
    """Network problem (timeout, DNS, blocked by network policy). Usually worth a retry."""


class UnsafeURLError(Exception):
    """URL points somewhere agents may not go (private address, non-http scheme)."""


@dataclass
class HttpResponse:
    url: str
    status_code: int
    content_type: str
    text: str
    truncated: bool


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsafeURLError(f"Alleen http(s)-adressen zijn toegestaan: {url}")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise WebAccessError(f"Domein niet gevonden: {parsed.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UnsafeURLError(f"Adres niet toegestaan (intern netwerk): {parsed.hostname}")


def _client(timeout: float) -> httpx.Client:
    s = get_settings()
    return httpx.Client(timeout=timeout, follow_redirects=False,
                        headers={"User-Agent": s.research_user_agent, "Accept-Language": "nl,en;q=0.5"})


def get(url: str, *, timeout: float | None = None, max_bytes: int = 600_000, check_url=_check_url) -> HttpResponse:
    timeout = timeout or get_settings().research_fetch_timeout
    current = url
    with _client(timeout) as client:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            try:
                with client.stream("GET", current) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        current = urljoin(current, resp.headers["location"])
                        continue
                    chunks, size, truncated = [], 0, False
                    for chunk in resp.iter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= max_bytes:
                            truncated = True
                            break
                    raw = b"".join(chunks)[:max_bytes]
                    text = raw.decode(resp.encoding or "utf-8", errors="replace")
                    return HttpResponse(str(resp.url), resp.status_code, resp.headers.get("content-type", ""), text,
                                        truncated)
            except httpx.HTTPError as exc:
                raise WebAccessError(f"{type(exc).__name__} bij ophalen van {urlparse(current).hostname}") from exc
    raise WebAccessError("Te veel doorverwijzingen")


def post_form(url: str, data: dict, *, timeout: float = 100.0, check_url=_check_url) -> HttpResponse:
    check_url(url)
    with _client(timeout) as client:
        try:
            resp = client.post(url, data=data)
        except httpx.HTTPError as exc:
            raise WebAccessError(f"{type(exc).__name__} bij {urlparse(url).hostname}") from exc
        return HttpResponse(str(resp.url), resp.status_code, resp.headers.get("content-type", ""), resp.text, False)
