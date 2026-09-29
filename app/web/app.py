"""FastAPI application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import create_all, init_engine
from app.services.audit import configure_logging
from app.web.deps import redirect, render
from app.web.security import LoginRequired

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


def _user_or_none(request: Request):
    """Best-effort user lookup for error pages (never raises)."""
    try:
        from app.db import session_factory
        from app.models import User

        uid = request.session.get("uid")
        if not uid:
            return None
        with session_factory()() as db:
            user = db.get(User, uid)
            if user is not None:
                db.expunge(user)
            return user
    except Exception:
        return None


def _sync_agents() -> None:
    from app.agents.registry import sync_agents
    from app.db import session_factory

    with session_factory()() as db:
        sync_agents(db)


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging()
    init_engine(database_url)
    create_all()
    _sync_agents()

    app = FastAPI(title="Energy Invoice Recovery", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, session_cookie="er_session",
        max_age=settings.session_max_age_seconds, same_site="lax", https_only=settings.is_production,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/app"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.is_production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return redirect("/login")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return render(request, "error.html", status_code=400, title="Onvolledig formulier",
                      detail="Niet alle verplichte velden zijn ingevuld. Ga terug en probeer het opnieuw.")

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        titles = {403: "Hier heeft u geen toegang toe", 404: "Deze pagina bestaat niet",
                  413: "Het bestand is te groot", 405: "Deze actie is hier niet mogelijk"}
        detail = exc.detail if exc.status_code not in (404, 405) and isinstance(exc.detail, str) else None
        if exc.status_code == 403 and detail in (None, "Forbidden"):
            detail = "Uw account heeft geen rechten voor deze pagina. Denkt u dat dit niet klopt? Neem contact op."
        return render(request, "error.html", status_code=exc.status_code, ico="lock" if exc.status_code == 403
                      else "search", title=titles.get(exc.status_code, "Er ging iets mis"), detail=detail,
                      user=_user_or_none(request))

    @app.exception_handler(Exception)
    async def _server_error(request: Request, exc: Exception):
        logging.getLogger("app").exception("unhandled error on %s", request.url.path)
        return render(request, "error.html", status_code=500, ico="alert", title="Er ging iets mis aan onze kant",
                      detail="Uw gegevens zijn niet gewijzigd. Probeer het over een ogenblik opnieuw; blijft het "
                             "misgaan, neem dan contact met ons op.", retry=str(request.url.path),
                      user=_user_or_none(request))

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    from app.web import (
        routes_admin,
        routes_auth,
        routes_cases,
        routes_clients,
        routes_ops,
        routes_public,
        routes_review,
    )

    for module in (routes_public, routes_auth, routes_clients, routes_review, routes_cases, routes_admin, routes_ops):
        app.include_router(module.router)
    return app
