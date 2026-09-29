# Factuurspoor agent architecture

Agents are part of the Factuurspoor application itself, not separate services. They use the same database,
authentication, audit log, design system and business logic as the rest of the platform. Staff manage them at
`/app/ops` (AI Operations).

## Where things live

The requested `/agents`, `/workflows` and `/integrations` folders are inside the `app` package, so they can
import the existing models and services directly:

| Folder | Contents |
|---|---|
| `app/agents/` | Agent framework plus the agents themselves |
| `app/workflows/` | Workflow definitions: the order of stages and the handoffs between agents |
| `app/integrations/` | Connections to the outside world. Currently `web/`: safe HTTP, OpenStreetMap, websites, and a test source |
| `app/models/agents.py` | Database tables |
| `app/services/agent_ops.py`, `app/web/routes_ops.py`, `app/web/templates/ops/` | AI Operations dashboard |

## Building blocks

| # | Component | File | What it does |
|---|---|---|---|
| 1 | Agent registry | `agents/catalog.py`, `agents/registry.py` | Defines all 16 agents in code (`AgentSpec`). At startup, `sync_agents()` writes their identity to the `agents` table. Runtime state (status, enabled, last run) is kept. |
| 2 | Configuration | `AgentSpec` | Holds id, name, description, role, group, system instructions (shared work rules included), permissions, tools, task types, version and approval actions. |
| 3 | Task queue | `agents/queue.py` | Queue stored in the database. It has priority (1–9), `not_before` for back-off, and an atomic claim (`UPDATE … WHERE status = QUEUED`), so several workers can run safely. |
| 4 | Execution | `agents/runner.py` | Runs each task inside a savepoint. On failure, everything the agent wrote is rolled back. Status, output, error and duration are recorded. Temporary web errors are retried with back-off. Unexpected errors are scrubbed. |
| 5 | Agent-to-agent | `ExecutionContext.handoff()` | Creates a child task for the next agent (same workflow run) and adds an `AgentMessage` of kind HANDOFF. |
| 6 | Shared context | `ExecutionContext.shared(scope)` | Key/value store with scopes `global`, `workflow:<run>` and `client:<id>`. Reads and writes are permission-checked and logged. |
| 7 | Permissions | `agents/permissions.py`, `agents/tools.py` | Least privilege. A tool works only if it is in the agent's toolset *and* the agent holds the tool's permission. Violations fail the task and are logged as `permission.denied`. |
| 8 | Execution logs | `agent_logs` table | Records every step, tool call (arguments, summary, duration), decision and error. Logs are kept even when a task's writes are rolled back. While a task runs, the steps are also visible live (`agents/live.py`). |
| 9 | Approvals | `ExecutionContext.require_approval()`, `agents/approvals.py` | The task pauses (`WAITING_APPROVAL`). When a staff member approves, it goes back into the queue and the agent continues. A rejection (with a required note) cancels it. `email.send` and `claims.submit` *always* require approval. |
| 10 | Agent status | `agents.status` | IDLE, RUNNING, WAITING, FAILED or DISABLED. An agent that is not built yet shows as "Nog niet gebouwd" and does not claim tasks. Work handed to it waits in the queue. |

Every task outcome is also written to the existing audit log (`agent.task_completed` and so on). Starting a task,
approving and disabling an agent are recorded with the user who did it.

### Running tasks

- **In the web app** (default, `ER_AGENTS_AUTORUN=true`): after a task is created or approved, the queue is
  processed in the background. There is also a "Wachtrij verwerken" button.
- **As a separate worker:** `python -m app.cli agents worker`. To process the queue once, use
  `python -m app.cli agents run`.

## Workflows

`app/workflows/catalog.py`:

- **lead_generation**: Lead Researcher → Lead Qualifier → Contact Researcher → Outreach → Email (approval) →
  Follow-up
- **invoice_recovery**: Invoice Intake → Invoice Analysis → Audit → Recovery → Claims (approval) → Finance

An agent asks the workflow for the next stage, so agents don't hard-code their successor.

## The 16 agents

| Group | Agents | Status |
|---|---|---|
| Coördinatie | Orchestrator, QA, Analytics | Defined |
| Acquisitie | **Lead Researcher**, Lead Qualifier, Contact Researcher, Outreach, Email, Follow-up | Lead Researcher is working. The rest are defined. |
| Facturen | Invoice Intake, Invoice Analysis, Audit | Defined. The existing ingestion and detection services are their future tools. |
| Terugvordering | Recovery, Claims, Customer Success, Finance | Defined |

"Defined" means the agent has identity, instructions, permissions, tools (declared, `handler=None`) and task types,
and appears on the dashboard. It becomes active once an implementation is added to `registry._implementations()`.

## Lead Researcher (working end to end)

1. **Input:** sectors (9 energy-intensive sectors with OpenStreetMap tags), a municipality or province, a maximum per
   sector, and optionally "only with a website".
2. **Approval:** jobs over 50 companies in total (`ER_RESEARCH_APPROVAL_THRESHOLD`) wait for a staff decision.
3. **Search:** `web.search_businesses` sends a query to the OpenStreetMap Overpass API. Only businesses in NL are kept,
   and each gets a source URL.
4. **Deduplication:** `prospects.find_duplicate` matches on OSM id, domain, or name plus city.
5. **Website:** `web.fetch_website` reads at most 15 sites per task. It collects the title, description, KvK number
   and multiple-location signals. SSRF protection is built in: only http(s), public IPs, redirects checked again,
   and a size limit.
6. **Fit score:** a fixed, explainable heuristic. Every point has a written reason (for example +15 for multiple
   locations and −15 for a chain branch). It is not a prediction.
7. **Save:** `prospects` table with evidence (source URL plus retrieval time). Only business data is stored, no
   personal data.
8. **Handoff:** companies with a score ≥ 55 (`ER_LEAD_QUALIFY_THRESHOLD`) are passed as one task to the Lead
   Qualifier.
9. **Result:** a structured summary is stored in `task.output`, plus shared context (`prospect_ids` and
   `lead_research.last_run`).

### Research sources

| `ER_RESEARCH_PROVIDER` | Source | Use |
|---|---|---|
| `openstreetmap` (default) | Real Overpass API plus the companies' own websites | Normal use; needs internet |
| `mock` | Test source with no internet | Development and tests. Every company is marked `[TESTDATA]`, uses `.example` domains, and has `is_test_data = true`. The UI shows a TESTDATA badge. |

## Database changes

New tables (created automatically at startup): `agents`, `agent_tasks`, `agent_logs`, `agent_messages`,
`agent_approvals`, `agent_context` and `prospects`. `create_all()` now also adds missing nullable columns to
existing tables (`add_missing_columns`), so existing SQLite databases keep working.

## Adding a new agent

1. Write `class MyAgent(Agent)` with `spec = <spec from catalog>` and `run(self, ctx) -> dict`.
2. Implement the tools it needs in `agents/toolbox.py`: set `handler` and `summarize`, and set `needs_db` if the tool
   uses the database.
3. Register it in `registry._implementations()`.
4. Add tests next to `tests/test_agents.py`.
