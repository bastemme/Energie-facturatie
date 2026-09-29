# Energy Invoice Recovery Engine — Product Architecture

Status: MVP foundation · Last updated: 2026-09-29

## 1. Product overview

We audit Dutch business energy invoices (electricity and gas) for billing errors and
missed corrections, and help clients recover overpaid amounts on a **no cure, no pay**
basis. We only earn a success fee on money that is actually recovered.

The product is an **auditable financial reconciliation system with AI-assisted
analysis**, not a chatbot. Every conclusion must be traceable to a source document,
page and line, and must be reproducible by deterministic code.

### The five value states (never mixed)

| # | State | Meaning | Where stored |
|---|-------|---------|--------------|
| 1 | Anomaly | Something looks unusual (e.g. consumption spike). Not proof. | `anomalies.classification = ANOMALY` |
| 2 | Potential billing error | A deterministic mismatch (invoice ≠ contract, qty × price ≠ amount, …). Needs verification. | `anomalies.classification = POTENTIAL_ERROR`, `anomalies.potential_recovery` |
| 3 | Confirmed billing error | A reviewer verified the evidence. | `anomalies.review_status = CONFIRMED` |
| 4 | Recoverable amount | Amount the supplier accepted (or that we claim after verification). | `recovery_cases.confirmed_amount` |
| 5 | Recovered | Money actually credited/paid to the client. The **only** basis for our fee. | `recovery_cases.recovered_amount` |

The UI, reports and dashboards always show these as separate figures with separate labels.

## 2. System architecture

A single deployable Python service (modular monolith). Cheap to run, easy to audit,
easy to split later.

```
                ┌──────────────────────────────────────────────────────────┐
 Browser ──────▶│ FastAPI (server-rendered Jinja, no SPA)                  │
 (staff/client) │  web/: landing · intake · client portal · review · admin │
                ├──────────────────────────────────────────────────────────┤
                │ services/: analysis · cases · fees · reports ·           │
                │            correspondence · audit · retention · metrics  │
                ├───────────────┬──────────────┬───────────────────────────┤
                │ ingestion/    │ extraction/  │ detection/ (rules engine) │
                │ validate,     │ pdf text +   │ one module per rule;      │
                │ encrypt,      │ positions,   │ reconciliation primitives │
                │ classify      │ NL parsers,  │ produce actual/expected/  │
                │               │ CSV/XLSX     │ difference/evidence       │
                ├───────────────┴──────────────┴───────────────────────────┤
                │ domain/: Decimal money, periods, EAN, confidence, fees   │
                ├──────────────────────────────────────────────────────────┤
                │ SQLAlchemy 2 → PostgreSQL (prod) / SQLite (dev, tests)   │
                │ Encrypted file store (Fernet, local disk; S3 later)      │
                │ ai/: provider interface, OFF by default                  │
                └──────────────────────────────────────────────────────────┘
```

**Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Pydantic v2, Jinja2, pdfplumber
(text + word positions + page rendering), openpyxl (Excel), reportlab (PDF output),
argon2 (passwords), cryptography/Fernet (encryption at rest), pytest.

Why this stack: the repository was empty, so there was nothing to preserve. Python has
the best document-extraction ecosystem, exact `Decimal` arithmetic, and pure-Python PDF
generation. Server rendering avoids a JS build pipeline and keeps the attack surface small.

## 3. Data model

All money is `Decimal`, never float. On PostgreSQL it is stored as `NUMERIC`. On SQLite
it is stored as an exact string (`app/db_types.py`). Amounts are EUR excluding VAT
unless a field says otherwise.

Tenancy and users
- `clients`: company name, KvK, contact, supplier(s), annual spend, `success_fee_percentage`
  (required, per client, no hardcoded default), `retention_days`, consent record
  (text version + timestamp + user), `ai_processing_allowed` (default false).
- `users`: `role ∈ {ADMIN, REVIEWER, CLIENT}`. CLIENT users are bound to exactly one `client_id`.
- `leads`: landing-page/outreach leads (source, UTM, consent), separate from clients.

Documents and provenance
- `documents`: client_id, original filename, sha256, mime, size, `doc_type`
  (INVOICE, CREDIT_NOTE, CONTRACT, METER_DATA, INVOICE_TABLE, OTHER, UNKNOWN),
  `status` (UPLOADED → PROCESSED / NEEDS_REVIEW / NEEDS_OCR / FAILED), page count, error.
- `extracted_values`: one row per extracted field. It records the value, raw text, page,
  bbox, confidence and method (REGEX, TABLE, CSV, MANUAL, LLM_VERIFIED). This is the
  provenance trail behind "value / source_document / page / location / confidence /
  extraction_method".

Normalized energy data
- `invoices`: client_id, document_id, supplier, invoice_number, invoice_type (INVOICE /
  CREDIT_NOTE), invoice_date, billing_period_start/end, EAN, meter_number, commodity
  (ELECTRICITY/GAS/MIXED), subtotal_excl_vat, vat_amount, total_incl_vat, currency,
  `corrects_invoice_number`, extraction_confidence, `verified_by`/`verified_at`.
- `invoice_lines`: invoice_id, category (normalized enum, see §5), description, quantity,
  unit, unit_price, amount, vat_rate, period_start/end, source_page, source_text,
  source_bbox, extraction_method, confidence.
- `meter_readings`: client_id, EAN, meter_number, register, reading_date, reading,
  reading_type (ACTUAL/ESTIMATED/UNKNOWN), multiplier, source (document + page/row).
- `contracts` + `contract_prices`: supplier, start/end, notes; each price row has a
  category, unit, price, valid_from/valid_to and source reference (document/page).
  Indexation is represented as dated price rows. The rows are entered and verified by a
  reviewer, so no free-text contract is interpreted automatically.
- `reference_rates`: time-dependent legal rates (VAT, energy-tax brackets). Each row
  has a source URL/citation, valid_from/to, and `verified_by`. **The system ships
  without tax rates.** An admin must enter them from the official source.

Findings and recovery
- `analysis_runs`: one per engine run: rule versions, counts, duration.
- `anomalies`: client_id, invoice_id, invoice_line_id, rule_id + rule_version, category,
  classification (ANOMALY/POTENTIAL_ERROR), severity, confidence (HIGH/MEDIUM/LOW),
  title, explanation, actual/expected/difference + unit, `potential_recovery`,
  calculation steps (JSON), evidence (JSON list of source refs), `requires_verification`,
  `review_status` (OPEN, INVESTIGATING, INFO_REQUESTED, CONFIRMED, REJECTED, DUPLICATE,
  RESOLVED), reviewer and notes, `fingerprint` (stable across re-runs, prevents
  duplicate findings), case_id.
- `recovery_cases`: client_id, supplier, status (lifecycle §8), disputed_amount,
  confirmed_amount, recovered_amount, success_fee_percentage (snapshot from client at
  creation), success_fee, created/submitted/closed timestamps. A case groups one or
  more confirmed anomalies for one supplier. That matches how a claim letter is sent.
- `case_events`: every status transition, amount change or note, with its user and time.
- `audit_log`: security-relevant actions (login, view/download document, export,
  delete, review decisions). IDs only, never financial values.

## 4. Processing pipeline

```
upload ─▶ validate (size, extension, magic bytes) ─▶ sha256 dedupe ─▶ encrypt & store
      ─▶ classify doc type ─▶ extract ─▶ normalize ─▶ persist with provenance
      ─▶ (low confidence ⇒ NEEDS_REVIEW queue) ─▶ reviewer verifies/corrects
      ─▶ analysis run (all detectors) ─▶ anomalies ─▶ review ─▶ cases ─▶ reports
```

Extraction strategy, in order of reliability:
1. **Structured tables** (CSV/XLSX in the canonical invoice-line or meter-reading format,
   Dutch or English headers). These are deterministic and fastest at scale, since many
   suppliers and brokers can export them.
2. **Text-layer PDFs**: pdfplumber words with coordinates are grouped into lines. Dutch
   label regexes find header fields. A line-item parser handles
   `omschrijving · hoeveelheid · eenheid · tarief · bedrag`. Every value keeps its page
   and bbox so the reviewer can see it highlighted on the rendered page. Supplier-specific
   parsers plug in via a registry once we have real sample invoices per supplier.
3. **Scanned PDFs**: detected (no text layer) and set to `NEEDS_OCR`. They are never
   guessed. An OCR adapter can be added (ocrmypdf/tesseract). Until then they go to
   manual entry.
4. **LLM extraction (optional, off)**: only for clients with `ai_processing_allowed`.
   Each returned value must occur literally in the page text or it is discarded.

Validation after extraction (deterministic): EAN check digit, period start ≤ end,
Σ lines ≈ subtotal, subtotal + VAT ≈ total. Failures lower confidence and route the
invoice to review. They never silently "fix" values.

## 5. Anomaly engine

A registry of independent detector modules. Each is a small pure function over an
`AnalysisContext` (the client's invoices, lines, contracts, meter readings, reference
rates and settings) and returns `Finding`s. Detectors never touch the DB. The analysis
service persists findings idempotently using a fingerprint.

Each finding embeds a **Reconciliation** (`actual`, `expected`, `difference`, `reason`,
`evidence[]`, `confidence`) and a list of human-readable calculation steps.

| Detector | Compares | Classification | Typical confidence |
|---|---|---|---|
| `line_arithmetic` | qty × unit price vs line amount | POTENTIAL_ERROR | HIGH |
| `invoice_totals` | Σ lines vs subtotal; subtotal + VAT vs total | POTENTIAL_ERROR | HIGH/MEDIUM |
| `vat` | VAT amount vs base × stated rate; stated rate vs verified reference | POTENTIAL_ERROR, requires verification | MEDIUM |
| `contract_price` | invoice unit price vs contract price valid in period | POTENTIAL_ERROR | HIGH if contract row verified |
| `fixed_charges` | fixed fee vs contract, duplicate fixed fees, fees outside contract period | POTENTIAL_ERROR | MEDIUM/HIGH |
| `billing_periods` | overlaps, duplicates, gaps per EAN/commodity | POTENTIAL_ERROR (overlap) / ANOMALY (gap) | MEDIUM |
| `duplicate_invoices` | same number; same EAN+period+total; duplicate lines | POTENTIAL_ERROR | HIGH/MEDIUM |
| `meter_consumption` | invoiced kWh/m³ vs (end − start) × multiplier; negative; estimated readings | POTENTIAL_ERROR / ANOMALY | HIGH/LOW |
| `consumption_trend` | daily consumption vs client's own history (ratio to median) | ANOMALY only | LOW |
| `credit_reconciliation` | credit note vs original invoice / rebill | POTENTIAL_ERROR | MEDIUM |
| `energy_tax` | tax rate per bracket vs **verified** reference rates | POTENTIAL_ERROR, always requires verification | MEDIUM at most |

Line categories (normalized, assigned by Dutch keyword rules): electricity supply
normal/low/single, feed-in, gas supply, fixed supply fee, network fixed/capacity/
transport, meter rental, energy tax electricity/gas, tax reduction, surcharge,
discount, correction, other.

### Confidence

Confidence is computed from evidence quality, not from model output:
- **HIGH**: every compared value is an explicit document value (or a verified contract
  or reference row), and the calculation is arithmetic-only.
- **MEDIUM**: it depends on a normalization or inference (e.g. category mapping,
  period prorating), or the invoice extraction itself was not yet verified.
- **LOW**: statistical/heuristic (trends, spikes) or based on estimated readings.

Tax findings are capped at MEDIUM and always carry `requires_verification = true` plus
the reference-rate id/version they used.

### Amount language

Potential amounts are always phrased "Mogelijke discrepantie: € X — verificatie vereist".
Only reviewer-confirmed findings may be called "geconstateerde onjuistheid".

## 6. Human review

The review queue is filterable by client, status, confidence and rule. For each finding
the reviewer sees the explanation, calculation, actual/expected/difference, and the
evidence list. Each evidence item links to **"Toon factuurpagina"**, which renders the
source PDF page server-side with the source region highlighted (or the source row for
CSV/XLSX). Actions: confirm, reject, investigate, request information, mark duplicate,
mark resolved. Each action requires a note for reject/duplicate and is written to the
audit trail.

## 7. Recovery calculation and fees

- `potential_recovery`: gross overpayment computed by the detector (≥ 0, excl. VAT
  unless the line is VAT-inclusive). Undercharges are recorded with potential_recovery = 0.
- `disputed_amount` = Σ potential_recovery of the anomalies in a case at submission.
- `confirmed_amount` is entered from the supplier's response.
- `recovered_amount` is entered when money or a credit is actually received (with a
  reference).
- `success_fee = round_half_up(recovered_amount × success_fee_percentage / 100, 2)`.
  The percentage is snapshotted per case from the client's agreement. Nothing is hardcoded.

## 8. Case lifecycle

```
DETECTED → REVIEW → VERIFIED → CLIENT_APPROVAL → SUBMITTED → SUPPLIER_REVIEW
         → NEGOTIATION → APPROVED → RECOVERED → CLOSED
side exits: REJECTED · DISPUTED · INSUFFICIENT_EVIDENCE (→ REVIEW to reopen)
```
Transitions are enforced by an explicit state machine (`app/services/cases.py`).
`RECOVERED` requires `recovered_amount > 0`, and `APPROVED` requires `confirmed_amount`.

## 9. Outputs

- **Client report (PDF):** separate totals for potential, confirmed and recovered;
  findings table; disclaimers.
- **Supplier claim package (ZIP):** a Dutch claim letter plus claim specification (PDF),
  structured case data (JSON), and the original source invoices.
- **Correspondence:** deterministic Dutch templates, factual and non-accusatory. The
  wording depends on verification state. An optional LLM polish step never changes
  numbers; its output is diffed for numeric tokens and rejected if they change.

## 10. Security model

- Authentication: email + password (argon2id), signed session cookie (HttpOnly,
  SameSite=Lax, Secure in production), CSRF token on every POST, login throttling.
- Authorization: role checks per route. **Tenant isolation**: every data access for a
  CLIENT user goes through `scope_to_user()`, which filters on `client_id`. Tests assert
  cross-tenant access returns 404.
- Files: validated by extension and magic bytes with a size cap, then encrypted at rest
  (Fernet, key from env). They are stored under random names outside any static
  directory and served only through authorized routes with `Content-Disposition:
  attachment` and `X-Content-Type-Options: nosniff`.
- Secrets only come from env (`.env` is git-ignored). The app refuses to start in
  production with default secrets.
- Logging: a filter strips currency amounts and IBAN/EAN-like digit runs. Business
  data is never logged.
- Audit log for logins, document views/downloads, exports, deletions and review decisions.
- Security headers: CSP (self only), X-Frame-Options DENY, Referrer-Policy.

## 11. GDPR design

- Data is mostly company data, but contact persons and sole-proprietor invoices are
  personal data. The lawful basis for client data is contract performance (art. 6(1)(b)).
  For leads it is legitimate interest or consent. **Legal texts (DPA /
  verwerkersovereenkomst, privacy statement) must be drafted or reviewed by a lawyer.
  The code does not make legal claims.**
- Data minimization: only energy-relevant fields are normalized. No bank details are extracted.
- Retention: `retention_days` per client (configurable default). A retention job
  (`python -m app.cli retention`) deletes documents and derived data after an engagement
  ends and keeps only aggregated, non-identifying metrics and the audit record of deletion.
- Right of access / portability: JSON export per client. Right to erasure: a hard-delete
  function (documents, extracted data, findings). It is confirmed and audited.
- Subprocessors: hosting (EU region), optional AI provider. AI processing is **off
  per client by default** and must be enabled only after the DPA covers it.
- Hosting: EU region required (e.g. a Dutch/EU VPS or managed Postgres in the EU).

## 12. AI boundaries

AI **may**: classify documents, help extract difficult layouts (verified against the
page text), summarize evidence, explain a finding in plain Dutch, and draft correspondence.

AI **may not**: be the source of any number used in a calculation, compute totals,
percentages, taxes or fees, set a review status, or confirm an error.

Implementation: `app/ai/provider.py` defines the interface. `NullProvider` is the
default. Every AI call is gated by `settings.ai_enabled AND client.ai_processing_allowed`
and is logged (without content) with token cost for margin analytics.

## 13. MVP scope (this iteration)

In scope:
1. Client intake (staff-created and public intake form) with consent capture and fee percentage.
2. Upload of PDF/CSV/XLSX with validation, encryption and classification.
3. Deterministic extraction: canonical CSV/XLSX, text PDFs (generic Dutch parser),
   meter-reading CSV. Manual correction of invoice header and lines.
4. Normalized data model with provenance.
5. Detection engine with the detectors in §5.
6. Review dashboard with evidence and page rendering with highlight.
7. Recovery cases with lifecycle, amounts and fees.
8. Client report PDF, supplier claim package (PDF + JSON + attachments).
9. Client dashboard, admin dashboard, core metrics.
10. Dutch landing page with lead capture.
11. Synthetic invoice generator and extensive tests.
12. Dockerfile + docker-compose (app + Postgres).

Explicitly out of scope for now: OCR, supplier-specific PDF parsers (need real samples),
interval (15-min) meter data, CRM/LinkedIn/email integrations (a lead model and webhook
hook exist), multi-currency, S3 storage, SSO/2FA.

## 14. Future architecture

- Background worker (RQ/Celery or Postgres-backed queue) for extraction and analysis
  once volumes reach thousands of invoices per client. Today these run synchronously
  per request and are fast for text PDFs.
- Supplier template library built from real invoices, with a regression corpus.
- OCR via ocrmypdf and a layout model, still with literal-match verification.
- P4 / EDSN metering data import (with client authorization) as the strongest evidence source.
- Object storage (S3-compatible, EU) with envelope encryption per client.
- 2FA for staff, SSO for larger clients.
- CRM sync (webhook-out of lead/case events), partner referral tracking with fee sharing.
- Alembic migrations once the schema stabilizes after the first real clients (the MVP
  uses `create_all`).

## 15. Key technical decisions

| Decision | Choice | Why |
|---|---|---|
| Architecture | Modular monolith | One deployable, cheap, auditable. The module boundaries allow a later split. |
| Language | Python 3.11 | Extraction ecosystem, Decimal, PDF generation. |
| UI | Server-rendered Jinja | No build step, small attack surface, fast iteration. |
| Money | `Decimal` everywhere, exact DB storage | No float rounding in financial logic. |
| Rules | One module per detector, versioned | Explainable, testable, and findings record rule version. |
| Findings dedupe | Fingerprint (rule + invoice + line + key) | Re-running analysis never duplicates or overwrites review decisions. |
| Tax rates | Admin-entered with source, none shipped | Never fabricate legal rules; they are time-dependent. |
| Contracts | Reviewer-entered price rows with source page | Contract language varies too much to trust automatic interpretation. |
| AI | Optional, off by default, never a source of numbers | Privacy and auditability. |
| Files | Fernet-encrypted local disk | Simple and secure at MVP scale. The interface allows S3 later. |
| DB | SQLite dev/test, Postgres prod | Zero-setup dev. Production-grade in deployment. |
