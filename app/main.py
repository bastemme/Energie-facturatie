"""ASGI entry point: `uvicorn app.main:app`."""

from app.web.app import create_app

app = create_app()
