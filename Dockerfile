FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

RUN pip install uv==0.8.17 \
 && useradd --create-home --uid 10001 app

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev --extra postgres \
 && mkdir -p /data && chown app:app /data

USER app
ENV ER_ENVIRONMENT=production ER_DATA_DIR=/data PATH="/srv/.venv/bin:$PATH"
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
# --proxy-headers: trust X-Forwarded-* from the reverse proxy (needed for correct client IPs in audit/rate limits)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
