/* Factuurspoor Operations: the command center.

   One state, one reducer, two sources:
     LIVE  polls /app/ops/api/live (snapshot + events derived from the real tables)
     DEMO  plays /app/ops/api/demo (a scripted, fictitious run; development only)
   Both feed apply(event). The mode is always visible, and demo state is thrown away when you return to live. */
(() => {
  "use strict";
  const root = document.querySelector("[data-ops]");
  if (!root) return;
  const boot = JSON.parse(document.getElementById("ops-boot").textContent);
  const topo = boot.topology;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, r = root) => r.querySelector(s);
  const $$ = (s, r = root) => Array.from(r.querySelectorAll(s));
  const nl2 = new Intl.NumberFormat("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nl0 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 });
  const fmt = (v, kind) => (kind === "eur" ? "€ " + nl2.format(v || 0) : nl0.format(v || 0));
  const hms = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleTimeString("nl-NL", { hour12: false }); };
  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeOut = (t) => 1 - Math.pow(1 - t, 3);
  const wait = (ms) => new Promise((r) => setTimeout(r, reduce ? 0 : ms));
  const name = (id) => topo.names[id] || id || "Systeem";
  const mono = (id) => { const p = name(id).replace(/-/g, " ").split(/\s+/); return ((p[0] || "?")[0] + (p[1] ? p[1][0] : (p[0] || "??")[1] || "")).toUpperCase(); };
  const SVGNS = "http://www.w3.org/2000/svg";

  /* Tiny DOM builder: text always goes through textContent (no HTML injection from event messages). */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v; else if (k === "text") el.textContent = v; else el.setAttribute(k, v === true ? "" : v);
    }
    kids.flat().forEach((k) => { if (k !== null && k !== undefined && k !== false) el.append(k.nodeType ? k : document.createTextNode(String(k))); });
    return el;
  }
  const svgEl = (tag, attrs) => { const el = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };
  const check = () => { const s = svgEl("svg", { viewBox: "0 0 16 16", "aria-hidden": "true" }); s.append(svgEl("path", { d: "M3.5 8.5l3 3 6-7", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" })); return s; };

  /* ======================================================================= state */
  const state = { mode: "live", agents: {}, metrics: {}, case: null, seen: new Set(), stream: [], liveEvents: [], panel: null };
  const WORD = { queued: "Wachtend", waiting: "Beoordeling nodig", failed: "Fout", done: "Afgerond", idle: "Beschikbaar", disabled: "Uitgeschakeld", planned: "Nog niet gebouwd" };
  const wordFor = (id, st) => (st === "running" ? (topo.activity[id] || "Actief") : WORD[st] || st);

  /* ======================================================================= stage geometry */
  const stage = $("[data-stage]"), svg = $("[data-edges]"), fx = $("[data-fx]"), token = $("[data-token]");
  const nodes = {};
  $$("[data-node]").forEach((n) => { nodes[n.dataset.node] = n; });
  let paths = {};

  function anchor(id) {
    const ring = nodes[id] && nodes[id].querySelector(".n-ring");
    if (!ring) return null;
    const r = ring.getBoundingClientRect(), s = stage.getBoundingClientRect();
    return { x: r.left - s.left + r.width / 2, y: r.top - s.top + r.height / 2, r: r.width / 2 + 3 };
  }
  function edgeD(a, b) {
    const dy = b.y - a.y, dx = b.x - a.x;
    if (Math.abs(dy) < 8) {
      const dir = Math.sign(dx) || 1;
      const x1 = a.x + dir * a.r, x2 = b.x - dir * b.r;
      if (Math.abs(dx) < 260) return `M${x1},${a.y} L${x2},${b.y}`;
      const lift = Math.min(70, Math.abs(dx) * 0.22);
      return `M${a.x},${a.y - a.r} C${a.x},${a.y - a.r - lift} ${b.x},${b.y - b.r - lift} ${b.x},${b.y - b.r}`;
    }
    const down = dy > 0 ? 1 : -1;
    const y1 = a.y + down * a.r, y2 = b.y - down * b.r, my = (y1 + y2) / 2;
    return `M${a.x},${y1} C${a.x},${my} ${b.x},${my} ${b.x},${y2}`;
  }
  function drawEdges() {
    const s = stage.getBoundingClientRect();
    svg.setAttribute("viewBox", `0 0 ${s.width} ${s.height}`);
    svg.replaceChildren();
    paths = {};
    const layer = svgEl("g", {}), gates = svgEl("g", {});
    topo.edges.forEach((e) => {
      const a = anchor(e.source), b = anchor(e.target);
      if (!a || !b) return;
      const p = svgEl("path", { d: edgeD(a, b), class: `e ${e.kind}` });
      const t = svgEl("title", {}); t.textContent = `${name(e.source)} → ${name(e.target)}: ${e.label}`; p.append(t);
      layer.append(p);
      paths[`${e.source}>${e.target}`] = { el: p, kind: e.kind };
      if (e.kind === "approval") {
        const len = p.getTotalLength(), m = p.getPointAtLength(len / 2);
        gates.append(svgEl("rect", { x: m.x - 4, y: m.y - 4, width: 8, height: 8, class: "gate", transform: `rotate(45 ${m.x} ${m.y})` }));
      }
    });
    // Ambient shimmer along the recovery lane: the system is on, nothing is claimed about data.
    const lane = ["invoice_intake", "invoice_analysis", "audit", "recovery", "claims", "finance"].map(anchor).filter(Boolean);
    if (lane.length > 1 && !reduce) {
      layer.prepend(svgEl("path", { class: "amb", d: "M" + lane.map((p) => `${p.x},${p.y}`).join(" L") }));
    }
    svg.append(layer, gates);
    positionToken(false);
  }

  /* ======================================================================= effects */
  function pulseNode(id, cls, ms = 900) {
    const n = nodes[id];
    if (!n || reduce) return;
    n.classList.remove(cls); void n.offsetWidth; n.classList.add(cls);
    setTimeout(() => n.classList.remove(cls), ms);
  }
  /* A data packet physically travels along the connection; the receiving node reacts when it arrives. */
  function travel(from, to, label) {
    const a = anchor(from), b = anchor(to);
    if (!a || !b) return Promise.resolve();
    let entry = paths[`${from}>${to}`];
    let temp = null;
    if (!entry) { temp = svgEl("path", { d: edgeD(a, b), class: "e handoff" }); svg.prepend(temp); entry = { el: temp, kind: "handoff" }; }
    const path = entry.el, len = path.getTotalLength();
    path.classList.add("hot");
    if (reduce) {
      setTimeout(() => { path.classList.remove("hot"); temp && temp.remove(); }, 500);
      return Promise.resolve();
    }
    const halo = svgEl("circle", { r: 9, class: "pk-halo" }), dot = svgEl("circle", { r: 3.6, class: `pk ${entry.kind}` });
    svg.append(halo, dot);
    const tag = label ? h("span", { class: "pk-label", text: label.length > 38 ? label.slice(0, 36) + "…" : label }) : null;
    if (tag) fx.append(tag);
    const dur = Math.max(700, Math.min(1400, len * 2.2));
    return new Promise((resolve) => {
      const t0 = performance.now();
      const step = (now) => {
        const t = Math.min(1, (now - t0) / dur), pt = path.getPointAtLength(len * easeInOut(t));
        dot.setAttribute("cx", pt.x); dot.setAttribute("cy", pt.y); halo.setAttribute("cx", pt.x); halo.setAttribute("cy", pt.y);
        if (tag) tag.style.setProperty("transform", `translate(${pt.x}px, ${pt.y}px) translate(-50%, -160%)`);
        if (t < 1) { requestAnimationFrame(step); return; }
        dot.remove(); halo.remove(); tag && tag.remove();
        pulseNode(to, "hit", 700);
        setTimeout(() => { path.classList.remove("hot"); temp && temp.remove(); }, 600);
        resolve();
      };
      requestAnimationFrame(step);
    });
  }
  /* Evidence particles: small sparks converge on the agent that found a source. */
  function sparks(id, count = 6, cls = "") {
    const c = anchor(id);
    if (!c || reduce) return;
    for (let i = 0; i < count; i++) {
      const ang = Math.random() * Math.PI * 2, dist = 55 + Math.random() * 60;
      const s = h("i", { class: `spark ${cls}` });
      fx.append(s);
      const x0 = c.x + Math.cos(ang) * dist, y0 = c.y + Math.sin(ang) * dist;
      s.animate([{ transform: `translate(${x0}px, ${y0}px)`, opacity: 0 }, { opacity: 1, offset: 0.35 },
        { transform: `translate(${c.x}px, ${c.y}px) scale(.4)`, opacity: 0 }],
      { duration: 800 + Math.random() * 300, delay: i * 45, easing: "cubic-bezier(.2,.7,.2,1)", fill: "forwards" })
        .finished.then(() => s.remove(), () => s.remove());
    }
  }
  function amountChip(id, text, cls = "") {
    const c = anchor(id);
    if (!c) return;
    const chip = h("span", { class: `amt-chip ${cls}`, text });
    chip.style.setProperty("left", `${c.x}px`); chip.style.setProperty("top", `${c.y - c.r - 34}px`);
    fx.append(chip);
    setTimeout(() => chip.remove(), 3000);
  }

  /* ======================================================================= agents */
  function setAgent(id, patch) {
    const n = nodes[id];
    const a = Object.assign(state.agents[id] || {}, patch);
    state.agents[id] = a;
    if (!n) return;
    const st = a.state || "idle";
    n.dataset.state = st;
    const word = a.word || wordFor(id, st);
    const w = n.querySelector(".n-word");
    if (w.textContent !== word) { w.textContent = word; if (!reduce) { w.classList.remove("changed"); void w.offsetWidth; w.classList.add("changed"); } }
    n.querySelector("[data-n-task]").textContent = st === "running" && a.task ? a.task.title : "";
    const prog = a.progress && a.progress[1] ? `${a.progress[0]} / ${a.progress[1]}` : "";
    n.querySelector("[data-n-step]").textContent = st === "running" ? [a.step || "Gestart", prog].filter(Boolean).join(" · ") : "";
    n.querySelector("[data-n-bar]").style.setProperty("transform", `scaleX(${a.progress && a.progress[1] ? a.progress[0] / a.progress[1] : st === "running" ? 0.08 : 0})`);
    n.setAttribute("aria-label", `${name(id)}: ${word}`);
    const running = Object.values(state.agents).filter((x) => x.state === "running").length;
    stage.style.setProperty("--activity", String(Math.min(1, running / 3)));
    if (state.panel === id) renderPanel();
  }
  function agentsFromSnapshot(agents) {
    Object.entries(agents || {}).forEach(([id, a]) => setAgent(id, { state: a.state, word: a.word, task: a.task, step: a.step, progress: a.progress, recent: a.recent, last: a.last }));
  }

  /* ======================================================================= metrics */
  const metricEls = $$("[data-metric]");
  function tween(el, from, to, kind, dur) {
    if (reduce || from === to) { el.textContent = fmt(to, kind); return; }
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      el.textContent = fmt(from + (to - from) * easeOut(p), kind);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function setMetrics(m, fromZero = false) {
    if (!m) return;
    metricEls.forEach((box) => {
      const key = box.dataset.metric;
      if (!(key in m)) return;
      const kind = box.dataset.format, to = Number(m[key]) || 0;
      const from = fromZero ? 0 : Number(box.dataset.value) || 0;
      box.dataset.value = String(to);
      const v = box.querySelector("[data-v]");
      if (!fromZero && from === to) { v.textContent = fmt(to, kind); return; }
      tween(v, from, to, kind, fromZero ? 1100 : 700);
      if (!fromZero && !reduce) { box.classList.remove("tick"); void box.offsetWidth; box.classList.add("tick"); }
    });
    state.metrics = m;
  }

  /* ======================================================================= the case */
  const TOKEN_AT = { received: "invoice_intake", extracted: "invoice_intake", analyzed: "invoice_analysis", finding: "invoice_analysis",
    audited: "audit", validation: "audit", recovery: "recovery", claim: "claims" };
  function positionToken(animate = true) {
    const c = state.case;
    if (!c) { token.hidden = true; return; }
    const target = TOKEN_AT[c.current] || "finance";
    const a = anchor(target), n = nodes[target];
    if (!a || !n) return;
    const s = stage.getBoundingClientRect(), nr = n.getBoundingClientRect();
    const wasHidden = token.hidden;
    token.hidden = false;
    const w = token.offsetWidth;
    const y = nr.bottom - s.top + 8;
    if (wasHidden && animate && !reduce) {
      token.style.setProperty("transition", "none");
      token.style.setProperty("transform", `translate(${-w - 10}px, ${y}px)`);
      token.style.setProperty("opacity", "0");
      void token.offsetWidth;
      token.style.removeProperty("transition");
    }
    token.style.setProperty("opacity", "1");
    token.style.setProperty("transform", `translate(${Math.max(4, Math.min(s.width - w - 4, a.x - w / 2))}px, ${y}px)`);
  }

  function renderCase(c, prev, animate) {
    const body = $("[data-case-body]");
    const link = $("[data-case-link]");
    link.replaceChildren();
    if (!c) {
      body.replaceChildren(h("div", { class: "oc-empty" }, h("b", { text: "Nog geen factuur in behandeling" }),
        h("span", { text: boot.demo ? "Upload facturen bij een klant, of speel de demo af." : "Upload facturen bij een klant; de agents nemen het vanaf daar over." })));
      return;
    }
    if (c.href && state.mode === "live") link.append(h("a", { href: c.href, text: "Open klant" }));
    const inv = c.invoice || {};
    const done = !c.current;
    const statusCls = done ? "done" : (c.current === "validation" ? "wait" : "");
    const head = h("div", { class: "lc-invoice" },
      h("div", { class: "lc-ref" }, inv.number ? `Factuur ${inv.number}` : c.client, h("small", { text: [inv.supplier || "Leverancier onbekend", c.client].filter(Boolean).join(" · ") })),
      h("div", { class: "lc-amount num" }, inv.amount ? fmt(inv.amount, "eur") : "—", h("small", { text: "factuurbedrag" })),
      h("span", { class: `lc-status ${statusCls}` }, h("i"), c.status || ""),
      c.synthetic && state.mode === "live" ? h("span", { class: "lc-synth", text: "Synthetische demo-klant uit de database" }) : null);
    const prevAgents = {};
    (prev && prev.agents || []).forEach((a) => { prevAgents[a.agent] = a.state; });
    const list = h("ol", { class: "lc-agents" }, (c.agents || []).map((a) => {
      const changed = animate && prev && prevAgents[a.agent] !== a.state;
      return h("li", { class: `${a.state}${changed ? " flash" : ""}` }, h("span", { class: "st" }, check()),
        h("b", { text: name(a.agent) }),
        h("span", { class: "n", text: a.note || { pending: "Wacht", queued: "In de wachtrij", active: "Bezig", done: "Klaar", waiting: "Wacht op goedkeuring", failed: "Mislukt" }[a.state] || "" }));
    }));
    body.replaceChildren(head, list);
  }
  const STAGE_NOTE = { received: "Document binnen", extracted: "Regels en velden uitgelezen", analyzed: "Controleregels uitgevoerd",
    audited: "Bron, berekening en zekerheid gecontroleerd", finding: "Afwijking met bedrag", validation: "Bevestigd door een specialist",
    recovery: "Dossier per leverancier", claim: "Claimbrief klaar voor indiening" };
  function renderTimeline(c, prev, animate) {
    const tl = $("[data-timeline]");
    const stages = c ? c.stages : topo.stages.map((s) => ({ key: s.key, label: s.label, state: "pending" }));
    const before = {};
    (prev && prev.stages || []).forEach((s) => { before[s.key] = s.state; });
    tl.replaceChildren(...stages.map((s) => h("li", { class: `${s.state}${animate && prev && before[s.key] !== s.state && s.state === "done" && !reduce ? " just" : ""}` },
      h("i", {}, check()), h("span", {}, h("b", { text: s.label }), h("small", { text: s.state === "pending" ? "" : STAGE_NOTE[s.key] || "" })))));
    $("[data-tl-sub]").textContent = c ? (c.invoice && c.invoice.number ? `Factuur ${c.invoice.number}` : c.client) : "Nog geen case";
  }

  function renderOpportunity(c, prev, animate) {
    const box = $("[data-opportunity]");
    const o = (c && c.opportunity) || { potential: 0, validated: 0, findings: 0 };
    const ev = c && c.evidence;
    const validated = o.validated > 0;
    const wasValidated = prev && prev.opportunity && prev.opportunity.validated > 0;
    box.classList.toggle("potential", o.potential > 0 && !validated);
    box.classList.toggle("validated", validated);
    if (validated && !wasValidated && animate && !reduce) { box.classList.remove("validated"); void box.offsetWidth; box.classList.add("validated"); }
    $("[data-op-label]").textContent = validated ? "Gevalideerde terugvordering" : "Mogelijke terugvordering";
    $("[data-op-status]").textContent = validated ? "Gevalideerd" : o.potential > 0 ? "In validatie" : o.findings ? "Bevinding" : "Geen bevinding";
    const amtEl = $("[data-op-amount]");
    const target = validated ? o.validated : o.potential;
    const before = prev && prev.opportunity ? (wasValidated ? prev.opportunity.validated : prev.opportunity.potential) : 0;
    if (animate && target !== before) tween(amtEl, before || 0, target || 0, "eur", 900); else amtEl.textContent = fmt(target, "eur");
    $("[data-op-conf]").textContent = ev ? ev.confidence : "—";
    $("[data-op-src]").textContent = ev ? `${ev.sources} ${ev.sources === 1 ? "bron" : "bronnen"}` : "0 bronnen";
    const claimed = c && c.stages && c.stages.some((s) => s.key === "claim" && s.state === "done");
    const on = { potential: o.potential > 0, validated, claimed, recovered: false };
    $$("[data-op-step]").forEach((li) => li.classList.toggle("on", !!on[li.dataset.opStep]));
  }

  function renderEvidence(c, prev, animate) {
    const ev = c && c.evidence;
    const prevNodes = {};
    (prev && prev.evidence && prev.evidence.nodes || []).forEach((n) => { prevNodes[n.key] = n.count; });
    $("[data-ev-title]").textContent = ev ? ev.title : "Nog geen bevinding";
    const fresh = [];
    $$("[data-ev]").forEach((li) => {
      const n = ev && ev.nodes.find((x) => x.key === li.dataset.ev);
      const count = n ? n.count : 0;
      li.classList.toggle("on", count > 0);
      li.querySelector("[data-ev-item]").textContent = count ? (count > 1 ? `${n.items[n.items.length - 1]} (+${count - 1})` : n.items[0]) : "niet gebruikt";
      if (count) li.dataset.tip = n.items.join(" · "); else delete li.dataset.tip;
      if (animate && count > (prevNodes[li.dataset.ev] || 0)) { fresh.push(li.dataset.ev); if (!reduce) { li.classList.remove("new"); void li.offsetWidth; li.classList.add("new"); } }
    });
    $("[data-ev-finding]").classList.toggle("on", !!(ev && ev.amount > 0));
    $("[data-ev-valid]").classList.toggle("on", !!(ev && ev.validated));
    drawEvidenceLines(fresh);
  }
  function drawEvidenceLines(fresh = []) {
    const g = $("[data-ev-graph]"), lines = $("[data-ev-lines]");
    if (!g || !lines || getComputedStyle(lines).display === "none") return;
    const gr = g.getBoundingClientRect();
    const pt = (el, side) => { const r = el.getBoundingClientRect(); return { x: (side === "l" ? r.left : r.right) - gr.left, y: r.top - gr.top + r.height / 2 }; };
    lines.setAttribute("viewBox", `0 0 ${gr.width} ${gr.height}`);
    lines.replaceChildren();
    const rootEl = $("[data-ev-root]"), fin = $("[data-ev-finding]"), val = $("[data-ev-valid]");
    const curve = (a, b) => `M${a.x},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x},${b.y}`;
    $$("[data-ev]").forEach((li) => {
      const on = li.classList.contains("on"), isNew = fresh.includes(li.dataset.ev) && !reduce;
      lines.append(svgEl("path", { d: curve(pt(rootEl, "r"), pt(li, "l")), class: `${on ? "on" : ""}${isNew ? " draw" : ""}`, pathLength: 1 }));
      lines.append(svgEl("path", { d: curve(pt(li, "r"), pt(fin, "l")), class: `${on ? "on" : ""}${isNew ? " draw" : ""}`, pathLength: 1 }));
    });
    const fr = pt(fin, "l"), vr = pt(val, "l"), fb = fin.getBoundingClientRect();
    const valid = val.classList.contains("on");
    lines.append(svgEl("path", { d: `M${fr.x + 14},${fb.bottom - gr.top} L${vr.x + 14},${vr.y - 12}`, class: valid ? "valid" : "" }));
  }

  function setCase(c, animate = true) {
    const prev = state.case;
    state.case = c || null;
    renderCase(state.case, prev, animate);
    renderTimeline(state.case, prev, animate);
    renderOpportunity(state.case, prev, animate);
    renderEvidence(state.case, prev, animate);
    const t = $("[data-token-ref]", token);
    if (state.case) {
      const inv = state.case.invoice || {};
      t.textContent = inv.number ? `Factuur ${inv.number}` : state.case.client;
      const o = state.case.opportunity || {};
      $("[data-token-amt]", token).textContent = o.potential > 0 ? fmt(o.validated || o.potential, "eur") : "";
      token.classList.toggle("valid", o.validated > 0);
    }
    positionToken(animate);
    if (state.panel) renderPanel();
  }

  /* ======================================================================= stream */
  const streamEl = $("[data-stream]"), streamEmpty = $("[data-stream-empty]");
  const KIND = { handoff: "handoff", "evidence.found": "evidence", "finding.created": "finding", "finding.validated": "valid",
    "approval.requested": "approval", "task.completed": "done", "task.failed": "failed", "demo.end": "done" };
  function streamText(ev) {
    if (ev.kind === "handoff") return `→ ${name(ev.to)}: ${ev.message}`;
    if (ev.kind === "task.started") return `Start: ${ev.message}`;
    return ev.message;
  }
  function addStream(ev, animate) {
    if (ev.kind === "task.progress" || ev.kind === "task.queued") return;
    const li = h("li", { class: `k-${KIND[ev.kind] || "step"}${animate && !reduce ? " in" : ""}` },
      h("time", { text: hms(ev.ts) }), h("span", { class: "who", text: mono(ev.agent) }),
      h("span", { class: "what" }, h("b", {}, name(ev.agent), ev.mode === "demo" ? h("span", { class: "demo-tag", text: "DEMO" }) : null), h("span", { text: streamText(ev) })));
    li.dataset.agent = ev.agent || "";
    streamEl.prepend(li);
    while (streamEl.children.length > 80) streamEl.lastChild.remove();
    state.stream.unshift(ev);
    state.stream.length = Math.min(state.stream.length, 200);
    streamEmpty.hidden = streamEl.children.length > 0;
    $("[data-stream-count]").textContent = state.mode === "demo" ? "gesimuleerd" : "laatste minuten";
    if (state.panel && state.panel === ev.agent) renderPanel(true);
  }

  /* ======================================================================= reducer */
  function apply(ev, animate = true) {
    const d = ev.data || {};
    if (d.metrics) setMetrics(d.metrics);
    if (d.case !== undefined) setCase(d.case, animate);
    const cur = state.agents[ev.agent] || {};
    switch (ev.kind) {
      case "task.queued":
        if (cur.state !== "running") setAgent(ev.agent, { state: "queued", word: null });
        break;
      case "handoff": {
        const go = () => { const t = state.agents[ev.to] || {}; if (t.state !== "running") setAgent(ev.to, { state: "queued", word: null, task: ev.task }); };
        if (animate) travel(ev.agent, ev.to, ev.message).then(go); else go();
        break;
      }
      case "task.started":
        setAgent(ev.agent, { state: "running", word: null, task: ev.task, step: null, progress: null });
        if (animate) pulseNode(ev.agent, "hit", 700);
        break;
      case "task.step":
        setAgent(ev.agent, { step: ev.message });
        break;
      case "task.progress":
        setAgent(ev.agent, { progress: [d.done, d.total] });
        break;
      case "task.completed":
        setAgent(ev.agent, { state: "done", word: null, step: null, progress: null, last: ev.message });
        if (animate) pulseNode(ev.agent, "done-flash", 900);
        break;
      case "task.failed":
        setAgent(ev.agent, { state: "failed", word: null, last: ev.message });
        break;
      case "task.paused": case "approval.requested":
        setAgent(ev.agent, { state: "waiting", word: null });
        break;
      case "evidence.found":
        if (animate) sparks(ev.agent, 7);
        break;
      case "finding.created":
        if (animate) { sparks(ev.agent, 10); amountChip(ev.agent, `Mogelijk ${fmt(d.amount, "eur")}`, "rev"); }
        break;
      case "finding.validated":
        if (animate) { pulseNode(ev.agent, "validate", 1300); sparks(ev.agent, 8, "pos"); amountChip(ev.agent, `Gevalideerd ${fmt(d.amount, "eur")}`); }
        break;
      default:
        break;
    }
    if (ev.kind === "task.step" && d.amount && animate) amountChip(ev.agent, fmt(d.amount, "eur"));
    addStream(ev, animate);
  }

  /* ======================================================================= live source */
  const player = { queue: [], busy: false };
  const GAP = { handoff: 750, "task.started": 420, "task.completed": 420, "task.step": 300, "task.progress": 120 };
  async function play() {
    if (player.busy) return;
    player.busy = true;
    while (player.queue.length && state.mode === "live") {
      if (player.queue.length > 24) { // a burst (e.g. a fast batch): catch up without animation, keep the last few animated
        player.queue.splice(0, player.queue.length - 8).forEach((ev) => apply(ev, false));
      }
      const ev = player.queue.shift();
      apply(ev, true);
      await wait(GAP[ev.kind] || 350);
    }
    player.busy = false;
    if (state.mode === "live" && state.pendingSnapshot) { reconcile(state.pendingSnapshot); state.pendingSnapshot = null; }
  }
  function reconcile(snap) {
    agentsFromSnapshot(snap.agents);
    setMetrics(snap.metrics);
    if (JSON.stringify(snap.case) !== JSON.stringify(state.case)) setCase(snap.case, true);
    $("[data-stage-summary]").textContent = `${snap.operational} van ${snap.total} agents operationeel`;
  }
  function ingest(events, animate) {
    const fresh = (events || []).filter((e) => !state.seen.has(e.id));
    fresh.forEach((e) => state.seen.add(e.id));
    fresh.sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
    state.liveEvents.push(...fresh);
    if (state.liveEvents.length > 300) state.liveEvents.splice(0, state.liveEvents.length - 300);
    if (!animate) { fresh.forEach((e) => addStream(e, false)); return; }
    player.queue.push(...fresh);
    play();
  }
  let polling = false, pollTimer = null;
  async function poll() {
    if (state.mode !== "live" || polling || document.hidden) return;
    polling = true;
    try {
      const r = await fetch("/app/ops/api/live", { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      if (state.mode !== "live") return;
      ingest(data.events, true);
      if (player.busy || player.queue.length) state.pendingSnapshot = data.snapshot; else reconcile(data.snapshot);
      root.classList.remove("is-stale");
    } catch (_) {
      root.classList.add("is-stale");
    } finally { polling = false; }
  }
  function startPolling() { clearInterval(pollTimer); pollTimer = setInterval(poll, 2000); }

  /* ======================================================================= demo source (same reducer) */
  const badge = $("[data-mode-badge]"), banner = $("[data-demo-banner]"), playBtn = $("[data-demo-play]");
  let demoTimers = [];
  function setMode(mode) {
    state.mode = mode;
    root.classList.toggle("is-demo", mode === "demo");
    badge.classList.toggle("demo", mode === "demo"); badge.classList.toggle("live", mode === "live");
    $("[data-mode-label]").textContent = mode === "demo" ? "DEMO" : "LIVE";
    banner.hidden = mode !== "demo";
    $("[data-stream-count]").textContent = mode === "demo" ? "gesimuleerd" : "laatste minuten";
  }
  function clearStage() {
    Object.keys(nodes).forEach((id) => setAgent(id, { state: "idle", word: null, task: null, step: null, progress: null, recent: [], last: null }));
    streamEl.replaceChildren(); state.stream = [];
    streamEmpty.hidden = false;
    setCase(null, false);
  }
  async function startDemo() {
    let script;
    try {
      const r = await fetch("/app/ops/api/demo", { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      script = await r.json();
    } catch (_) { return; }
    demoTimers.forEach(clearTimeout); demoTimers = [];
    player.queue = [];
    setMode("demo");
    clearStage();
    if (playBtn) playBtn.disabled = true;
    script.events.forEach((ev) => {
      demoTimers.push(setTimeout(() => {
        if (state.mode !== "demo") return;
        ev.ts = new Date(Date.now()).toISOString();
        apply(ev, true);
        if (ev.kind === "demo.end" && playBtn) { playBtn.disabled = false; $("[data-demo-play-label]").textContent = "Opnieuw afspelen"; }
      }, reduce ? Math.min(ev.at, 200) : ev.at));
    });
  }
  async function stopDemo() {
    demoTimers.forEach(clearTimeout); demoTimers = [];
    setMode("live");
    if (playBtn) { playBtn.disabled = false; $("[data-demo-play-label]").textContent = "Demo afspelen"; }
    clearStage();
    // Back to real data only: rebuild from what the live source has seen, then fetch a fresh snapshot.
    state.liveEvents.slice(-60).forEach((e) => addStream(e, false));
    try {
      const r = await fetch("/app/ops/api/live", { credentials: "same-origin" });
      const data = await r.json();
      ingest(data.events, false);
      reconcile(data.snapshot);
      setMetrics(data.snapshot.metrics);
    } catch (_) { location.reload(); }
  }
  if (playBtn) playBtn.addEventListener("click", startDemo);
  const stopBtn = $("[data-demo-stop]");
  if (stopBtn) stopBtn.addEventListener("click", stopDemo);

  /* ======================================================================= side panel (level 2 → 3) */
  const layer = document.querySelector("[data-ops-layer]"); // outside .page: fixed positioning must not be clipped
  const panel = $("[data-agent-panel]", layer), scrim = $("[data-panel-scrim]", layer);
  const $p = (s) => layer.querySelector(s);
  let lastFocus = null;
  function openPanel(id) {
    state.panel = id;
    renderPanel();
    if (panel.hidden) {
      lastFocus = document.activeElement;
      panel.hidden = false; scrim.hidden = false;
      requestAnimationFrame(() => { panel.classList.add("open"); scrim.classList.add("show"); });
    }
    panel.focus({ preventScroll: true });
  }
  function closePanel() {
    if (panel.hidden) return;
    state.panel = null;
    panel.classList.remove("open"); scrim.classList.remove("show");
    setTimeout(() => { panel.hidden = true; scrim.hidden = true; }, reduce ? 0 : 300);
    if (lastFocus) lastFocus.focus({ preventScroll: true });
  }
  function renderPanel(fresh = false) {
    const id = state.panel;
    if (!id) return;
    const a = state.agents[id] || {};
    const st = a.state || "idle";
    $p("[data-ap-mono]").textContent = mono(id);
    $p("[data-ap-name]").textContent = name(id);
    $p("[data-ap-role]").textContent = topo.roles[id] || "";
    const stateEl = $p("[data-ap-state]");
    stateEl.className = `ap-state ${st}`;
    stateEl.replaceChildren(h("i"), a.word || wordFor(id, st));
    const sec = (k, ...kids) => h("div", { class: "ap-sec" }, h("span", { class: "k", text: k }), ...kids);
    const parts = [];
    if (a.task && (st === "running" || st === "queued" || st === "waiting")) {
      const prog = a.progress && a.progress[1] ? `${a.progress[0]} / ${a.progress[1]}` : null;
      parts.push(sec("Huidige taak", h("span", { class: "big", text: a.task.title }),
        h("dl", { class: "ap-kv" }, h("dt", { text: "Taak" }), h("dd", { text: a.task.number ? `#${a.task.number}` : "—" }),
          h("dt", { text: "Stap" }), h("dd", { text: a.step || (st === "running" ? "Gestart" : "Nog niet gestart") }),
          prog ? h("dt", { text: "Voortgang" }) : null, prog ? h("dd", { text: prog }) : null)));
    } else {
      parts.push(sec("Laatste uitkomst", h("span", { text: a.last || "Nog geen taken uitgevoerd." })));
    }
    const c = state.case;
    const inCase = c && (c.agents || []).find((x) => x.agent === id);
    if (inCase) {
      const ev = c.evidence;
      parts.push(sec("In de live case", h("dl", { class: "ap-kv" },
        h("dt", { text: "Invoer" }), h("dd", { text: c.invoice && c.invoice.number ? `Factuur ${c.invoice.number}` : c.client }),
        h("dt", { text: "Status" }), h("dd", { text: inCase.note || inCase.state }),
        h("dt", { text: "Bewijs ontvangen" }), h("dd", { text: ev ? `${ev.sources} ${ev.sources === 1 ? "bron" : "bronnen"}` : "—" }),
        h("dt", { text: "Zekerheid" }), h("dd", { text: ev ? ev.confidence : "—" }))));
    }
    const conn = (dir, list) => list.map((e) => {
      const b = h("button", { type: "button" }, h("span", { class: "dir", text: dir }), h("b", { text: name(e.other) }), h("span", { class: "lbl", text: e.label }));
      b.addEventListener("click", () => openPanel(e.other));
      return b;
    });
    const incoming = topo.edges.filter((e) => e.target === id).map((e) => ({ other: e.source, label: e.label }));
    const outgoing = topo.edges.filter((e) => e.source === id).map((e) => ({ other: e.target, label: e.label }));
    parts.push(sec("Verbonden agents", h("div", { class: "ap-links" }, conn("←", incoming), conn("→", outgoing),
      !incoming.length && !outgoing.length ? h("span", { class: "small muted", text: "Geen vaste verbindingen." }) : null)));
    const mine = state.stream.filter((e) => e.agent === id && e.kind !== "task.progress").slice(0, 8);
    const recent = mine.length ? mine.map((e) => ({ ts: e.ts, message: streamText(e) })) : (a.recent || []).slice().reverse();
    parts.push(sec("Recente activiteit", recent.length
      ? h("ol", { class: "ap-recent" }, recent.map((r, i) => h("li", { class: fresh && i === 0 && !reduce ? "in" : "" }, h("time", { text: hms(r.ts) }), h("span", { text: r.message }))))
      : h("span", { class: "small muted", text: "Nog geen activiteit." })));
    if (state.mode === "live") {
      parts.push(sec("Hoe precies (trace)", h("div", { class: "ap-trace" },
        a.task ? h("a", { class: "btn btn-sm", href: `/app/ops/tasks/${a.task.id}`, text: "Uitvoeringstrace en logs" }) : null,
        h("a", { class: "btn btn-sm", href: `/app/ops/agents/${id}`, text: "Agent, tools en permissies" }))));
    } else {
      parts.push(sec("Hoe precies (trace)", h("span", { class: "small muted", text: "In de demo bestaat geen echte trace. In live-modus opent u hier de volledige uitvoering met logs en brongegevens." })));
    }
    $p("[data-ap-body]").replaceChildren(...parts);
  }
  Object.entries(nodes).forEach(([id, n]) => n.addEventListener("click", () => openPanel(id)));
  $p("[data-ap-close]").addEventListener("click", closePanel);
  scrim.addEventListener("click", closePanel);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });

  /* ======================================================================= start */
  setMode("live");
  agentsFromSnapshot(boot.snapshot.agents);
  setMetrics(boot.snapshot.metrics, true); // count up once on arrival
  ingest(boot.events, false);
  requestAnimationFrame(() => {
    drawEdges();
    setCase(boot.snapshot.case, false);
  });
  if ("ResizeObserver" in window) {
    let raf = 0;
    new ResizeObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { drawEdges(); drawEvidenceLines(); }); }).observe(stage);
  }
  window.addEventListener("resize", () => drawEvidenceLines());
  startPolling();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  if (boot.demo && new URLSearchParams(location.search).get("demo") === "1") setTimeout(startDemo, 600);
})();
