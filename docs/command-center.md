# Factuurspoor Operations (command center)

`/app/ops` shows the 16 existing agents as one working system: invoice → agents → evidence → finding →
recovery. The agents themselves did not change. The page only reads their state through a thin observability
layer.

## Three levels

| Level | Question | Where |
|---|---|---|
| 1 | What is happening? | The metrics, the agent network, the live case and the opportunity (all above the fold) |
| 2 | Why is it happening? | The evidence graph, the execution timeline and the activity stream. Clicking an agent opens the side panel with its current task, input, evidence, confidence, connected agents and recent activity |
| 3 | How exactly did it happen? | From the side panel: the task's execution trace and logs (`/app/ops/tasks/{id}`) and the agent's tools and permissions (`/app/ops/agents/{id}`). The "Werkvoorraad en details" section below holds the queue, approvals, errors and the lead pipeline |

## The agent network

Nodes and connections come from `app/agents/topology.py`. Every edge there corresponds to a real call in the
code, and `tests/test_observability.py` fails if a workflow step has no edge.

| Kind | Meaning | Example |
|---|---|---|
| `handoff` (solid) | `ctx.handoff()` to the next workflow stage | Invoice Intake → Invoice Analysis |
| `approval` (dashed, amber gate) | The next step only starts after a person decides | Audit → Recovery (a specialist confirms findings); Outreach → Email (a person approves the message) |
| `dispatch` (faint) | The Orchestrator starts a task | Orchestrator → Invoice Intake |
| `reads` (dotted) | An agent reads another agent's output | Claims → Customer Success; Email → Follow-up |

The lanes follow the catalog groups. The top lane reads as the product story:
**Factuur → Begrip → Verificatie → Kans → Terugvordering → Resultaat**
(Intake, Analysis, Audit, Recovery, Claims, Finance).

What you see:

- **A node works.** The ring pulses, two particles orbit it, and the current task, step and progress appear.
  The status word is specific: *Analyseert*, *Valideert*, *Stelt claim op*. See `ACTIVITY_NL` in
  `services/agent_ops.py`.
- **A handoff.** A packet travels along the real connection with a label (for example "184 factuurregels").
  On arrival the connection lights up, the receiving node reacts and its status changes. The event also enters
  the stream.
- **Evidence found.** Sparks converge on the agent, the matching source in the evidence graph lights up with a
  line drawn to the finding, and the confidence updates.
- **A finding or validation.** An amount rises above the node. When a specialist validates, the opportunity
  changes from POTENTIAL to VALIDATED (the amount animates, the colour turns green) and the invoice token turns
  green.
- **The invoice itself.** A token ("Factuur 2026-…") moves along the top lane to the agent that is working on
  the case.
- **Ambience.** When nothing happens, only the slow grid drift and a faint shimmer on the recovery lane move.
  The glow behind the network grows with the number of running agents.

Confidence is shown as *Hoog / Middel / Laag*, the way the detection rules store it. The page never shows an
invented percentage.

## Observability layer

`app/services/observability.py`:

- **Events.** `recent_events(db)` returns the events of the last 3 minutes, built from:
  - agent tasks (`handoff`, `task.queued`, `task.started`);
  - agent logs (`task.step`, `task.completed`, `task.failed`, `task.paused`);
  - the in-process live steps and progress of running tasks;
  - approvals (`approval.requested`).

  Every event has a stable id, so the browser de-duplicates and nothing is lost when logs arrive only at the
  end of a task. Tool calls stay out of the stream; they are in the trace (level 3).
- **Snapshot.** `snapshot(db)` returns the six metrics (from `services/metrics.py`, where potential, validated
  and recovered are never added together), the state of each agent (including its recent activity) and the
  live case.
- **Live case.** `live_case(db)` shows the most recent invoice-recovery run:
  - the invoice, the stages it has reached and the state of each case agent;
  - the evidence of its strongest finding, grouped into Contract, Tarief en regels, Verbruik, Historische
    facturen and Externe data;
  - the opportunity.

  The status never claims work that is not running ("Wacht op Audit").

Endpoints: `GET /app/ops/api/live` (polled every 2 s) and `GET /app/ops/api/demo`.

## LIVE and DEMO

`app/web/static/ops.js` has one state and one reducer, `apply(event)`, with two sources:

- **LIVE** polls the API. Events are played through a small queue with minimum spacing, so a burst of
  millisecond-fast tasks is still visible. Very large bursts catch up without animation.
- **DEMO** (development only, never available with `ER_ENVIRONMENT=production`) plays
  `services/simulation.py`: one fictitious invoice from arrival to a prepared claim in about 30 seconds, with
  a lead-generation stream running in parallel. It uses the same event schema, the same case and metrics shape,
  and the same reducer and components. Every demo event has `mode: "demo"`. The badge reads DEMO, a banner
  explains that nothing is real, every metric carries a "DEMO" prefix, stream items are tagged, and trace links
  are disabled. Nothing is written to the database. "Terug naar live" discards the demo state and reloads the
  real snapshot.

Start the demo with the **Demo afspelen** button, or open `/app/ops?demo=1`.

The demo figures are internally consistent. The difference is € 0,1248 − € 0,1182 = € 0,0066 per kWh. It
applies to 312.450 kWh on this invoice plus 1.578.620 kWh on six earlier invoices, and
1.891.070 kWh × € 0,0066 = € 12.481,06.

## Motion

The command center follows `docs/motion-design-system.md`:

- Packets take 700–1400 ms depending on the path length, with an in-out easing.
- Node reactions take 700–1200 ms, and number changes 700 ms (1100 ms for the count-up on arrival).
- Loops (pulse, orbit, breathing dot) only run while an agent actually works.
- With reduced motion, packets, particles, orbits and the grid drift are switched off. State changes are
  instant.
- On small screens the lanes wrap to three columns, the Orchestrator's dispatch lines are hidden and the live
  case moves under the network.
