"""FastAPI application factory."""

from __future__ import annotations

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


def create_app(database_url: str | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging()
    init_engine(database_url)
    create_all()

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
        titles = {403: "Geen toegang", 404: "Niet gevonden", 413: "Bestand te groot"}
        return render(request, "error.html", status_code=exc.status_code,
                      title=titles.get(exc.status_code, "Er ging iets mis"), detail=exc.detail
                      if exc.status_code != 404 else None)

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

    from app.web import routes_admin, routes_auth, routes_cases, routes_clients, routes_public, routes_review

    for module in (routes_public, routes_auth, routes_clients, routes_review, routes_cases, routes_admin):
        app.include_router(module.router)
    return app
