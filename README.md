# Energy Invoice Recovery Engine

An auditable reconciliation system for Dutch business energy invoices (electricity and gas).
It finds potential billing errors, lets a specialist verify them against the source documents,
and turns confirmed findings into recovery claims on a no cure, no pay basis.

> AI is infrastructure here, not the product. All financial logic is deterministic and reproducible.
> Every finding traces back to a document, page and line.

See **[docs/product-architecture.md](docs/product-architecture.md)** for the architecture, data model and
design decisions, **[docs/agent-architecture.md](docs/agent-architecture.md)** for the AI agents, and
**[docs/operations.md](docs/operations.md)** for deployment and operations.

## Quick look (no technical knowledge needed)

See **[HOE TE STARTEN.md](HOE%20TE%20STARTEN.md)**: download the ZIP, double-click the start file for your system, and log in with the demo account.

## What it does

```
upload → validate & encrypt → extract (PDF text / CSV / XLSX) → normalize with provenance
      → 11 detector modules → review (with highlighted source page) → recovery case
      → Dutch claim letter + claim package (PDF/JSON/attachments) → track recovered money → success fee
```

It keeps five money states apart: *anomaly → potential error → confirmed error → supplier-approved →
actually recovered*. The success fee is computed only from money actually recovered.

## Quick start (local)

```bash
uv sync --extra postgres
cp .env.example .env              # for local dev you can leave most values empty
echo "ER_ENVIRONMENT=development" >> .env
uv run python -m app.cli create-admin you@example.nl
uv run python -m app.cli seed-demo   # optional: SYNTHETIC demo client with known errors
uv run uvicorn app.main:app --reload
uv run python -m app.cli agents worker   # optional: separate agent worker (the web app also runs tasks)
# open http://127.0.0.1:8000  (landing page)  and  /login
```

## Tests

```bash
uv run pytest                        # SQLite
ER_TEST_DATABASE_URL=postgresql+psycopg://user:pw@host/db uv run pytest   # PostgreSQL
uv run ruff check app tests
```

The suite includes synthetic invoices (rendered as real PDFs) with known errors. One example:
1,000 kWh billed at €0.25 against a contract price of €0.20 must produce a €50.00 potential discrepancy.

## Layout

| Path | Purpose |
|---|---|
| `app/domain/` | Pure logic: Decimal money, periods, EAN check digit, units, confidence, fees |
| `app/models/` | SQLAlchemy models (exact decimals on every backend) |
| `app/ingestion/` | Upload validation, encrypted storage, processing pipeline |
| `app/extraction/` | Dutch number/date parsing, PDF text with coordinates, invoice parser, CSV/XLSX import |
| `app/detection/` | Rules engine: one module per detector, versioned, no DB/LLM access |
| `app/services/` | Analysis runs, review, cases (state machine), metrics, reports, correspondence, GDPR, audit |
| `app/agents/` | Agent framework (registry, queue, runner, permissions, approvals, logs) and the agents |
| `app/workflows/` | Workflow stages and handoffs between agents |
| `app/integrations/` | External sources (OpenStreetMap, company websites) behind a safe HTTP layer, plus a test source |
| `app/ai/` | AI boundary (off by default; never a source of numbers) |
| `app/web/` | FastAPI routes, Jinja templates (Dutch UI), security helpers |
| `app/devtools/synthetic.py` | Synthetic invoice generator (marked as such) |
| `docs/` | Architecture, import formats, operations |
