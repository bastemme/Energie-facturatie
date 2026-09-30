# Motion design system

Factuurspoor handles people's money and their suppliers' invoices. It should feel like financial
infrastructure, and it should also be pleasant to use. Motion is part of the interface: it must
**explain, guide, give feedback, establish hierarchy or build trust**. An animation that does none
of these is removed.

Character: **fast, precise, quiet.** No bounce, no elastic easing, no shaking loops, no sparkles
or lightning. Motion never delays the user: every transition can be interrupted, and a page is
usable while it animates.

Files:

| File | Contents |
|---|---|
| `app/web/static/motion.css` | Tokens and every shared motion rule (loaded on all pages) |
| `app/web/static/landing.css` | The hero product demo and the scroll story (public pages only) |
| `app/web/static/boot.js` | Runs before first paint. Sets `html.js` and `html.no-enter` so held states never flash |
| `app/web/static/app.js` | Behaviour: number tweening, draw-on-enter, dialogs, drawer, navigation progress, soft live updates, demo and story |

---

## 1. Tokens

### Durations

| Token | Value | Use |
|---|---|---|
| `--m-instant` | 90 ms | Press feedback, tooltip delay, checkbox scale |
| `--m-fast` | 140 ms | Hover, focus, colour changes, dialog exit |
| `--m-base` | 220 ms | Components entering or leaving: tabs, dialogs, disclosure, page content |
| `--m-slow` | 360 ms | Larger surfaces: drawer, entrance sequences, chart elements, reveals |
| `--m-story` | 640 ms | Storytelling steps (landing only) |
| `--stagger` | 36 ms | Delay between siblings in a sequence. At most 8 steps (≈ 250 ms in total) |

Number tweens take 520 ms (app) or 900 ms (landing amounts). A line chart draws in 900 ms.

### Easing

| Token | Curve | Use |
|---|---|---|
| `--e-out` | `cubic-bezier(.16, 1, .3, 1)` | Anything entering: fast start, soft landing |
| `--e-std` | `cubic-bezier(.2, .7, .2, 1)` | State changes in place (colour, background, opacity) |
| `--e-in` | `cubic-bezier(.5, 0, .75, 0)` | Anything leaving (dialog exit, toast dismissal, page out) |
| `--e-inout` | `cubic-bezier(.65, 0, .35, 1)` | Moving between two known places: line draw, scan, breathing dot |

`linear` is used only for continuous flows (the pulse on the data-flow line).

### Distance

An entrance travels `--lift` (6 px), and at most 12 px for reveals. Scale changes never go outside
0.96 to 1.0, except a count badge popping to 1.18 once.

---

## 2. Rules

1. **Animate only `transform` and `opacity`.** Colour, background and box shadow may transition on
   hover and state changes. Never animate layout properties such as width, height, top or margin.
   The single exception is the search field widening on focus.
2. **Enter with `--e-out`, leave with `--e-in`, and leave faster than you enter.** For example, a
   dialog enters in 220 ms and leaves in 140 ms.
3. **Once per navigation.** Entrance sequences run on the first paint of a page. Live updates
   (`html.no-enter`) never replay them.
4. **Only what changed moves.** A number counts only if its value differs from the last time this
   person saw it. A status chip animates only when its text changed.
5. **Loops only while work is happening.** The breathing dot and the travelling pulse mean
   "something is running right now". They stop when it stops.
6. **The finished state is the default.** With JavaScript off, reduced motion, or after the
   sequence ends, every element is in its final position. Animations only hold elements back
   (`html.js … :not(.step)`).
7. **No horizontal overflow, ever.** Elements that slide in do so inside a container that clips
   them, or they travel vertically.

---

## 3. Transition patterns

| Pattern | Implementation |
|---|---|
| **Page transition** | Cross-document View Transitions (`@view-transition { navigation: auto }`). The sidebar, top bar and site header are named, so they stay still while the content fades out (140 ms) and back in with a 4 px rise (220 ms). Browsers without support just navigate. |
| **Navigation progress** | A 2 px brand line at the top of the page (`.nav-progress`). It starts on a same-origin link click or form submit, eases to 72 %, and is reset when the next page shows (also after back/forward). |
| **Page content** | `.main > .page` rises 6 px over 220 ms. |
| **Entrance sequence** | Figures, the ops strip, pipeline, agent grid, findings, feed and task lists stagger their children (`--stagger`, at most 8). |
| **Reveal** | `[data-reveal]` below the fold fades in and rises 12 px once, when it scrolls into view. |
| **Soft live update** | Pages with `[data-pulse]` poll every 3 s. When something changes, only `#main` and the sidebar counts are replaced, with no reload, no entrance replay and the scroll position kept. Changed numbers count and flash, and changed chips pop once. |

---

## 4. Component motion

| Component | Motion |
|---|---|
| **Buttons** | Hover: background and shadow in 140 ms. Press: `translateY(1px) scale(.985)` in 60 ms. While loading: spinner, and the button is disabled. A trailing arrow shifts 2 px on hover. |
| **Navigation** | The active item has a 2 px brand bar that scales in (220 ms). Icons tint on hover. A count badge pops once when its number changes. |
| **Tabs** | Underline scales from the centre (220 ms). Hovering an inactive tab shows a grey underline. |
| **Segments and filters** | Press scales to .97. A filter with a value gets a brand border so active filters are visible at a glance. |
| **Table rows** | Background on hover in 140 ms. Rows never move. |
| **Cards** | Linked panels and agent cards lift 1 px and deepen their shadow on hover (pointer devices only). |
| **Drawer (mobile navigation)** | Slides in with `translateX` (360 ms, `--e-out`) while the scrim fades (220 ms). Focus moves into the drawer, and Escape closes it. |
| **Dialogs** | In: fade, rise 8 px and scale from .98 (220 ms). Out: 140 ms with `--e-in`, and the element is removed after the animation. The backdrop fades with it. |
| **Disclosure (`details`)** | Content fades in and drops 4 px (220 ms). |
| **Tooltips** | Appear after 60 ms, fading in with a 3 px rise (140 ms). |
| **Toasts** | Rise in (360 ms). They leave with a fade and slide, and are removed after 6 s or on close. Error toasts stay until closed. |
| **Success** | The check icon draws its stroke once (360 ms). |
| **Error** | One short horizontal nudge (−3, 2, −1 px), never repeated. |
| **Changed value** | A brand, green (up) or amber (down) tint that fades over 1.4 s. |
| **Status chips** | Colours cross-fade (220 ms). A changed chip scales from .92 once. |
| **Numbers** | `ui.num(value, key, fmt)` renders `[data-count-key]`. The value counts from the previous one (sessionStorage per page or global) with a cubic ease-out, using tabular figures. |
| **Charts** | Bars grow from the baseline with a 28 ms stagger. Line charts draw their path (`pathLength="1"`), then points and labels fade in. Funnel bars grow from the centre, and progress, meter and score bars from the left. Below the fold they wait (`.will-draw`) until they are 20 % in view. |
| **Timelines** | The spoor fills node by node (160 ms apart), and the case pipeline left to right (90 ms apart). |
| **AI Operations** | Each agent card says what the agent is doing: the task title, the live step (for example "Controleregels uitvoeren · 3 / 16") and a progress bar. The status word is specific: *Analyseert, Valideert, Zoekt bedrijven…*, *Wachtend*, *Beoordeling nodig*, *Afgerond*, *Fout*. The dot breathes only while the agent works. The pipeline strip carries a travelling pulse only while a step is active. |
| **Invoice evidence** | Hovering or focusing evidence highlights the matching region of the document (`[data-hl]`). |

---

## 5. The signature motif: the data-flow line

`ol.trace` shows **Contract → Factuur → Meter → Audit → Terugvordering**. It is the only
decorative motif in the product, and it always represents real data flowing:

- A node fills when its data has arrived. Brand means known, amber means a finding, green means
  confirmed or recovered.
- A segment fills (scaleX, 360 ms) only when both of its ends are known.
- `.flowing` adds a travelling pulse, used only while work is in progress.

The same idea appears as the AI Operations pipeline strip and the case lifecycle.

---

## 6. Landing page

### Hero product demo (`[data-demo]`)

Ten steps, applied as cumulative classes `s1`…`s10` by `app.js`. The whole sequence takes about
6.7 s and starts when the demo is 30 % in view.

| Step | ms | What happens |
|---|---|---|
| s1 | 0 | The invoice sheet rises in. The Factuur node fills. |
| s2 | 500 | A scan line passes over the sheet once. |
| s3 | 1300 | The extracted fields appear (45 ms stagger). Each source region briefly tints. |
| s4 | 2100 | Rate row: invoice against contract. The Contract node and segment fill. |
| s5 | 2900 | Consumption row: invoice against meter, ✓ Klopt. The Meter node fills. |
| s6 | 3700 | The rate row turns amber: + € 0,0066. The Audit node turns amber. |
| s7 | 4400 | The invoice line is highlighted on the sheet. |
| s8 | 5000 | The recovery amount counts from € 0,00 to € 842,19. |
| s9 | 5900 | The evidence appears (invoice page/line, contract article, meter data). |
| s10 | 6700 | *Dossier aangemaakt*: the check draws, the Terugvordering node turns green and the status dot stops breathing. |

The status line (`aria-live="polite"`) names each step. The **Opnieuw afspelen** button replays
the sequence. **Interactive invoice:** hovering or focusing an extracted field highlights its
region on the invoice and shows its value, confidence and source location. All figures are
fictitious and labelled as such. They are internally consistent: 127.605 kWh × € 0,0066 =
€ 842,19, and the lines total € 18.492,31.

### Scroll story (`[data-story]`)

Seven chapters on the left, one sticky stage on the right. A chapter becomes current when it
crosses the middle of the viewport (IntersectionObserver with a −45 % root margin), and the stage
gets the cumulative classes `c1`…`c7`. The interface itself transforms; it does not just fade:

1. **Contract**: the contract card arrives in the centre.
2. **Factuur**: the contract slides to the left and the invoice takes the centre.
3. **Meterdata**: the meter data joins on the right.
4. **Reconciliatie**: the compared values light up in all three cards, and reconciliation rows
   drop in under them.
5. **Afwijking**: the rate row and the rate values turn amber, while the matching rows dim.
6. **Terugvordering**: the amount counts up with its calculation.
7. **Claim**: the correction request slides up over the documents it was built from.

The **recovery journey** under the stage follows the money: € 0 → Mogelijk (c4) → Gedetecteerd
(c5) → Beoordeeld (c6) → Geclaimd → Teruggevorderd (c7, 500 ms and 1100 ms later).

---

## 6b. Command center (`/app/ops`)

The command center is the one place where motion is continuous, because the system it shows is
continuously at work. The same rules apply (see `docs/command-center.md` for the full description):

| Motion | Duration and easing | Meaning |
|---|---|---|
| Packet along a connection | 700–1400 ms (by path length), in-out | Data is handed from one agent to another |
| Node hit, done flash, validation sweep | 700 / 900 / 1200 ms, `--e-out` | Received, finished, validated |
| Ring pulse and orbiting particles | 1.8 s / 3.2 s loops | Only while the agent is running |
| Evidence sparks | about 800 ms, staggered by 45 ms | A source was found |
| Amount chip rising over a node | 2.8 s | A finding or a validated amount |
| Invoice token moving along the lane | 900 ms, in-out | Where the invoice is now |
| Metrics | 1100 ms count-up on arrival, then 700 ms when a value changes | New figures |
| Grid drift, glow, shimmer | 18–90 s | Ambient: the system is on. The glow grows with the number of running agents |

The count-up on arrival is a deliberate exception to "only what changed moves": the command center
is a status screen that is opened to see the current totals.

---

## 7. Reduced motion and small screens

`prefers-reduced-motion: reduce`:

- View transitions, entrance sequences, reveals, draw animations, loops (breathing dot, travelling
  pulse), press scaling, hover lifts and the error nudge are all switched off.
- The demo and the story jump straight to their finished state. Numbers are shown without
  counting.
- What stays: colour and state changes, focus rings, the highlight of a hovered field or evidence
  region, and dialogs opening and closing. They change instantly and still communicate.

Small screens (≤ 900 px):

- The drawer slides in from the left over a scrim.
- The story has no sticky stage: the finished stage is shown first, followed by the chapters as
  plain text.
- The demo and reconciliation rows restack into two lines. Hover-only effects apply only on
  pointer devices (`@media (hover: hover)`).
- Pages are checked at 390 px for horizontal overflow: `scrollWidth` must equal the viewport width.

---

## 8. Approved interaction patterns

Use these patterns, and add a new one only with a reason from the list at the top of this document.

- `ui.num(value, key, fmt='int'|'eur', scope='page'|'global')` for any figure that can change.
- `data-reveal` for a block below the fold on public pages. Do not use it in the app, where
  content must be visible immediately.
- `data-pulse="/url"` plus `data-pulse-v` on a page that should update itself.
- `data-state-key="…"` on a status whose change deserves attention.
- `data-hl` / `data-region` to link a value to its location in a source document.
- `.changed` (with `.up` / `.down`) for a one-off tint after a change.
- `.flowing` on a `.trace` or `.pipe` only while work is in progress.
- A dialog opens through `[data-dialog-open]` and closes through `[data-dialog-close]`, the backdrop or Escape, so the exit animation runs.

Not approved: parallax, auto-playing carousels, scroll hijacking, typing effects, confetti,
count-ups of numbers that did not change, looping attention animations on static content, and
WebGL or canvas backgrounds.
