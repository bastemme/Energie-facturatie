# Factuurspoor design system

The living reference is at `/app/designsysteem` (staff only). Tokens and components are in
`app/web/static/app.css`, components as Jinja macros in `app/web/templates/_ui.html`, and icons in `app/web/icons.py`.

## Principles

1. **Trust through traceability.** Every number can be followed back to its source. The recurring visual
   motif is the *spoor*: a thin track with nodes, running from bron → analyse → berekening → conclusie. It
   appears in the logo, the landing-page analysis, the finding view and the case pipeline.
2. **Money states never blur.** Mogelijk (amber), bevestigd (brand), teruggevorderd (green) are always
   separate figures with their own label. Potential amounts carry "verificatie vereist".
3. **Color has meaning or is absent.** Neutral means information, cobalt means interaction, green means
   confirmed or recovered, amber means needs review, and red means error. Status color is never used without a label.
4. **Summary before detail.** Each screen answers its main question in the first viewport. Detail sits
   behind tabs, a `<details>` disclosure, or a click-through.
5. **Quiet chrome, loud data.** Hairline borders, one shadow level for panels, no gradients, and no decoration
   that isn't data.

## Tokens

| Group | Tokens |
|---|---|
| Ink | `--ink` #0d1422 · `--ink-2` #334055 · `--ink-3` #5f6b7e · `--ink-4` #8d97a7 |
| Surfaces | `--canvas` #f5f6f8 · `--surface` #fff · `--sunken` #eef1f5 · `--line` #e4e8ee · `--line-2` #cfd6e0 |
| Brand (interaction) | `--brand` #2944d8 · `--brand-strong` · `--brand-soft` · `--brand-line` |
| Status | `--positive` #107453 · `--review` #9a5a04 (mark #e39a2d) · `--critical` #b3261e, each with `-soft` and `-line` |
| Type | Instrument Sans (UI, tabular figures via `font-variant-numeric`), Geist Mono (identifiers such as EAN and rule IDs) |
| Scale | display clamp(2.1–3.25rem) · hero figure clamp(2.2–3.1rem) · h1 26px · h2 19px · h3 16px · body 15px · small 13px · label 11px caps |
| Space | 4px base: 4 · 8 · 12 · 16 · 20 · 24 · 32 · 40 · 48 · 64 · 80 |
| Radius | 5 · 8 · 12 · pill |
| Motion | 120 / 200 / 420ms, `cubic-bezier(.2,.7,.2,1)`. All motion is disabled under `prefers-reduced-motion`. |

Fonts are self-hosted (SIL OFL, licenses in `static/fonts/`), so there are no third-party requests. That matters
for GDPR, and the strict CSP requires it.

## Components

| Component | Macro / class | Rule |
|---|---|---|
| Page header | `ui.page_head(title, sub, crumbs)` | One primary action at most |
| Figures band | `ui.figures(summary)` | Exactly one hero figure per view |
| Finding card | `ui.finding_card(a)` | Kind, confidence, one-sentence summary, amount, and evidence location. Actions are Bewijs, Bevestigen and Afwijzen (with a note, via dialog). |
| Split view | `.split` + `_source.html` | Source document left (sticky). The analysis on the right follows the spoor. Evidence ↔ highlighted region are linked on hover and focus. |
| Chips | `ui.chip(text, cls)` | neutral, brand, review, positive, critical, quiet |
| Confidence | `ui.conf(c)` | Three-bar meter plus a word, with a tooltip that explains the basis |
| Empty state | `ui.empty(icon, title, text)` | Says what will appear and what to do, never just "Geen data" |
| Dialog | `<dialog>` + `data-dialog-open` | Used for confirmations and short forms. Destructive actions always ask for confirmation. |
| Toasts | flash messages | Success auto-dismisses after 6s. Errors stay until closed. |
| Pipeline | `.pipeline` | Case lifecycle. Side exits are shown as alerts. |
| Charts | `view.bar_chart`, `view.line_chart`, `meter_bar` | Server-side SVG, round ticks, `<title>` tooltips. Amber only marks months with findings. |
| Processing | `form[data-upload]` + `.process` | Shows the real stages (upload progress, documents read, invoices and lines found, checks run, new findings) |

## Responsive behaviour

- **≤1100px:** the split view stacks with the source first, and the figures drop to 2 columns.
- **≤900px:** the sidebar becomes a drawer behind a menu button, and the pipeline turns vertical.
- **≤640px:** tables marked `.stack-sm` become labelled cards, and finding cards stack (via container query, also
  inside narrow columns on desktop).

## Review checklist (run after every UI change)

1. Is the most important number visually dominant, and is it the right one?
2. Can someone tell potential money from recovered money at a glance?
3. Does any color appear without a meaning, or any status without a label?
4. Are there empty, loading and error states, all in plain Dutch?
5. Does it work at 390px without horizontal scrolling?
6. Would a CFO trust this screen with their invoices?
