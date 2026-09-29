"""Authentication, CSRF, authorization and tenant isolation helpers."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.domain.enums import Role
from app.models import Client, User

_hasher = PasswordHasher()
MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Wachtwoord moet minimaal {MIN_PASSWORD_LENGTH} tekens zijn.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# Dummy hash so unknown e-mail addresses take as long as wrong passwords (no user enumeration).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def verify_dummy() -> None:
    verify_password(_DUMMY_HASH, "x")


@dataclass
class _Bucket:
    failures: int = 0
    locked_until: float = 0.0


class LoginThrottle:
    """In-process throttle. For multi-instance deployments move this to the DB or Redis."""

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def is_locked(self, key: str) -> bool:
        with self._lock:
            b = self._buckets.get(key)
            return bool(b and b.locked_until > time.monotonic())

    def fail(self, key: str) -> None:
        s = get_settings()
        with self._lock:
            b = self._buckets.setdefault(key, _Bucket())
            b.failures += 1
            if b.failures >= s.login_max_attempts:
                b.locked_until = time.monotonic() + s.login_lockout_seconds
                b.failures = 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


login_throttle = LoginThrottle()


class RateLimiter:
    """Simple sliding-window limiter for public forms (in-process)."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


lead_limiter = RateLimiter(limit=5, window_seconds=3600)


# ---------------------------------------------------------------- CSRF


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def verify_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    form = await request.form()
    sent = form.get("csrf_token") or request.headers.get("x-csrf-token")
    expected = request.session.get("csrf")
    if not expected or not sent or not secrets.compare_digest(str(sent), str(expected)):
        raise HTTPException(status_code=403, detail="Ongeldig of verlopen formulier. Herlaad de pagina.")


# ---------------------------------------------------------------- authentication / authorization


class LoginRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    user_id = request.session.get("uid")
    if not user_id:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        request.session.clear()
        return None
    if request.session.get("auth_at", 0) + get_settings().session_max_age_seconds < time.time():
        request.session.clear()
        return None
    return user


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise LoginRequired()
    return user


def require_staff(user: User = Depends(require_user)) -> User:
    if not user.is_staff:
        raise HTTPException(status_code=403, detail="Geen toegang.")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != Role.ADMIN:
        raise HTTPException(status_code=403, detail="Alleen voor beheerders.")
    return user


def login_session(request: Request, user: User) -> None:
    request.session.clear()  # new session on login (prevents fixation)
    request.session["uid"] = user.id
    request.session["auth_at"] = int(time.time())
    request.session["csrf"] = secrets.token_urlsafe(32)


def can_access_client(user: User, client_id: str | None) -> bool:
    if client_id is None:
        return False
    return user.is_staff or (user.role == Role.CLIENT and user.client_id == client_id)


def get_client_for(db: Session, user: User, client_id: str) -> Client:
    """Tenant isolation: returns 404 (not 403) so object existence is not revealed."""
    client = db.get(Client, client_id)
    if client is None or not can_access_client(user, client.id):
        raise HTTPException(status_code=404)
    return client


def get_owned(db: Session, model, object_id: str, user: User):
    obj = db.get(model, object_id)
    if obj is None or not can_access_client(user, getattr(obj, "client_id", None)):
        raise HTTPException(status_code=404)
    return obj
