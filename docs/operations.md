# Operations

## Deployment

1. Pick an EU host (for example a Dutch or German VPS, or a managed platform with an EU region) and an EU managed
   Postgres, or the `db` service in `docker-compose.yml`.
2. `cp .env.example .env`, then set `ER_SECRET_KEY`, `ER_STORAGE_ENCRYPTION_KEY` and `POSTGRES_PASSWORD`.
   In production the app refuses to start without these, or with SQLite.
3. `docker compose up -d --build`
4. `docker compose exec app python -m app.cli create-admin you@yourdomain.nl`
5. Put a TLS reverse proxy in front of it (Caddy is the simplest: `reverse_proxy 127.0.0.1:8000`). Session
   cookies are `Secure` in production, so plain HTTP will not work.
6. Before processing real client data, enter the legal reference rates under **Referentietarieven**
   (VAT, energy-tax brackets per year), each with its official source. Nothing is shipped.

## Scheduled jobs

| Job | Command | Suggested schedule |
|---|---|---|
| Retention (GDPR) | `python -m app.cli retention` | daily |
| Database backup | `pg_dump` (encrypted, EU storage) | daily, 30-day rotation |
| Document volume backup | snapshot of `/data` | daily |

## Key management

- `ER_STORAGE_ENCRYPTION_KEY` encrypts every stored document. **If you lose it, the documents cannot be
  recovered.** Keep it in a password manager or secrets manager, separate from the backups.
- To rotate the key, add a key-rotation command (decrypt with the old key, re-encrypt with the new one) before you
  change the value. Fernet supports `MultiFernet` for this.
- `ER_SECRET_KEY` signs sessions. Rotating it logs everyone out.

## Security checklist (per release)

- `uv run ruff check app tests && uv run pytest` (SQLite + Postgres in CI)
- Dependencies: `uv lock --upgrade` monthly and review the changelogs.
- Staff accounts: unique per person, minimum 12-character passwords, deactivate on leaving.
- Review the audit log (`/app/audit`) for unexpected exports and downloads.

## Known limitations (MVP) and next steps

- **Processing is synchronous.** Large uploads are processed in the request. Move to a worker queue
  (RQ/Celery or Postgres-based) with timeouts before handling thousands of PDFs per client.
- **No OCR.** Scanned documents are routed to `NEEDS_OCR` for manual entry.
- **Generic PDF parser.** Build supplier-specific parsers from real sample invoices (registry in
  `app/extraction/invoice_pdf.py`) and keep a regression corpus of anonymized real invoices.
- **Login throttling and the lead rate limit are in-process.** Move them to the DB or Redis for multi-instance deployments.
- **No self-service password reset or 2FA yet.** Staff create temporary passwords.
- **The schema uses `create_all`.** Introduce Alembic migrations before the first schema change on a production database.
- **Legal texts are not included.** The machtiging, DPA (verwerkersovereenkomst), privacy statement and terms must be
  written or reviewed by a lawyer.
