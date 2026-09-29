from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.models.base import utcnow
from app.services.audit import audit
from app.web.deps import client_ip, redirect, render
from app.web.security import (
    current_user,
    login_session,
    login_throttle,
    require_user,
    verify_csrf,
    verify_dummy,
    verify_password,
)

router = APIRouter()


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(current_user)):
    if user:
        return redirect("/app")
    return render(request, "auth/login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    email = email.strip().lower()
    key = f"{email}|{client_ip(request)}"
    if login_throttle.is_locked(key):
        return render(request, "auth/login.html", status_code=429,
                      error="Te veel mislukte pogingen. Probeer het over 15 minuten opnieuw.")
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    if user is None:
        verify_dummy()
    if user is None or not user.is_active or not verify_password(user.password_hash, password):
        login_throttle.fail(key)
        audit(db, "auth.login_failed", ip=client_ip(request), details={"known_user": user is not None})
        db.commit()
        return render(request, "auth/login.html", status_code=401, error="Onjuist e-mailadres of wachtwoord.")
    login_throttle.reset(key)
    login_session(request, user)
    user.last_login_at = utcnow()
    audit(db, "auth.login", user=user, ip=client_ip(request))
    db.commit()
    return redirect("/app")


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    audit(db, "auth.logout", user=user, ip=client_ip(request))
    db.commit()
    request.session.clear()
    return redirect("/login")
