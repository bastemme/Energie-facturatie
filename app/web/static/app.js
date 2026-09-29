/* Factuurspoor — progressive enhancement. Every page works without this file. */
(() => {
  "use strict";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const nl = new Intl.NumberFormat("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const csrf = () => ($('meta[name="csrf-token"]') || {}).content || "";

  /* ---------- toasts: auto-dismiss, closable ---------- */
  function dismiss(t) { t.classList.add("leaving"); setTimeout(() => t.remove(), 220); }
  $$(".toast").forEach((t) => {
    const btn = $(".close", t);
    if (btn) btn.addEventListener("click", () => dismiss(t));
    if (!t.classList.contains("error")) setTimeout(() => dismiss(t), 6000);
  });

  /* ---------- mobile navigation drawer ---------- */
  const side = $(".sidebar"), menuBtn = $("[data-menu]");
  if (side && menuBtn) {
    let scrim;
    const close = () => { side.classList.remove("open"); scrim && scrim.remove(); scrim = null; menuBtn.setAttribute("aria-expanded", "false"); };
    menuBtn.addEventListener("click", () => {
      if (side.classList.contains("open")) return close();
      side.classList.add("open"); menuBtn.setAttribute("aria-expanded", "true");
      scrim = document.createElement("div"); scrim.className = "scrim"; scrim.addEventListener("click", close);
      document.body.appendChild(scrim);
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  }

  /* ---------- public header border on scroll ---------- */
  const header = $(".site-header");
  if (header) {
    const onScroll = () => header.classList.toggle("scrolled", window.scrollY > 8);
    onScroll(); window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------- dialogs ---------- */
  $$("[data-dialog-open]").forEach((b) => b.addEventListener("click", (e) => {
    const d = document.getElementById(b.dataset.dialogOpen);
    if (!d || typeof d.showModal !== "function") return;
    e.preventDefault();
    const act = b.dataset.action; const input = act && $('input[name="action"]', d);
    if (input) input.value = act;
    const form = $("form", d); if (form && b.dataset.url) form.action = b.dataset.url;
    const title = $("[data-dialog-title]", d); if (title && b.dataset.title) title.textContent = b.dataset.title;
    d.showModal(); const f = $("textarea, input:not([type=hidden])", d); f && f.focus();
  }));
  $$("[data-dialog-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));

  /* ---------- button loading state on submit ---------- */
  $$("form").forEach((f) => f.addEventListener("submit", (e) => {
    const b = e.submitter; if (b && b.classList.contains("btn") && !f.hasAttribute("data-no-loading")) {
      setTimeout(() => b.classList.add("is-loading"), 0);
    }
  }));

  /* ---------- count-up for hero figures ---------- */
  if (!reduce && "IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((en) => {
      if (!en.isIntersecting) return; io.unobserve(en.target);
      const el = en.target, target = parseFloat(el.dataset.countup), prefix = el.dataset.prefix || "";
      if (!isFinite(target) || target === 0) return;
      const start = performance.now(), dur = 450;
      const step = (now) => {
        const p = Math.min(1, (now - start) / dur), eased = 1 - Math.pow(1 - p, 3);
        el.textContent = prefix + nl.format(target * eased);
        if (p < 1) requestAnimationFrame(step); else el.textContent = prefix + nl.format(target);
      };
      requestAnimationFrame(step);
    }), { threshold: .4 });
    $$("[data-countup]").forEach((el) => io.observe(el));
  }

  /* ---------- document sheets: skeleton until the page image has loaded ---------- */
  $$(".sheet img").forEach((img) => {
    const sheet = img.closest(".sheet");
    const done = () => sheet.classList.remove("skeleton");
    if (img.complete && img.naturalWidth) done(); else { img.addEventListener("load", done); img.addEventListener("error", () => {
      sheet.classList.remove("skeleton"); sheet.innerHTML = '<p class="source-empty">Deze pagina kon niet worden weergegeven. Download het origineel om het te bekijken.</p>';
    }); }
  });

  /* ---------- split view: link evidence ↔ highlighted region on the source page ---------- */
  $$("[data-hl]").forEach((ev) => {
    const ids = ev.dataset.hl.split(" ");
    const rects = () => ids.map((id) => document.getElementById(id)).filter(Boolean);
    const on = () => { rects().forEach((r) => r.classList.add("active")); ev.classList.add("active"); };
    const off = () => { rects().forEach((r) => r.classList.remove("active")); ev.classList.remove("active"); };
    ev.addEventListener("mouseenter", on); ev.addEventListener("mouseleave", off);
    ev.addEventListener("focus", on); ev.addEventListener("blur", off);
    ev.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      const page = ev.dataset.page, sheet = $(".sheet[data-page]");
      if (page && sheet && sheet.dataset.page !== page && ev.dataset.href) { window.location = ev.dataset.href; return; }
      const r = rects()[0]; if (r) r.closest(".sheet").scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "nearest" });
    });
  });

  /* ---------- upload with a visible processing pipeline ---------- */
  const up = $("form[data-upload]");
  if (up) {
    const zone = $(".dropzone", up), input = $('input[type=file]', up), list = $(".dz-files", up);
    const panel = document.getElementById(up.dataset.upload);
    const showFiles = () => { if (list) list.textContent = input.files.length ? Array.from(input.files).map((f) => f.name).join(", ") : ""; };
    input.addEventListener("change", showFiles);
    ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, () => zone.classList.remove("drag")));
    zone.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; showFiles(); } });

    up.addEventListener("submit", (e) => {
      if (!panel || !window.FormData || !input.files.length) return;
      e.preventDefault();
      const steps = $$("li[data-step]", panel), bar = $(".progress rect", panel), summary = $("[data-summary]", panel);
      const set = (name, state, detail) => {
        const li = steps.find((s) => s.dataset.step === name); if (!li) return;
        li.className = state; if (detail !== undefined) $(".detail", li).textContent = detail;
      };
      panel.hidden = false; up.hidden = true; summary.hidden = true;
      steps.forEach((s) => { s.className = ""; $(".detail", s).textContent = ""; });
      set("upload", "active");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", up.action); xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", (ev) => {
        if (ev.lengthComputable) { const pct = Math.round(ev.loaded / ev.total * 100); bar.setAttribute("width", pct + "%"); set("upload", "active", pct + "%"); }
      });
      xhr.upload.addEventListener("load", () => { set("upload", "done", ""); set("read", "active"); bar.setAttribute("width", "100%"); });
      xhr.addEventListener("load", () => {
        let data; try { data = JSON.parse(xhr.responseText); } catch (_) { data = null; }
        if (xhr.status >= 400 || !data) { fail(xhr.status === 413 ? "Het bestand is te groot." : "Er ging iets mis bij het verwerken. Probeer het opnieuw."); return; }
        const ok = data.files.filter((f) => f.ok), bad = data.files.filter((f) => !f.ok);
        set("read", ok.length ? "done" : "failed", ok.length + " van " + data.files.length + " document(en)");
        const inv = ok.reduce((n, f) => n + f.invoices, 0), lines = ok.reduce((n, f) => n + f.lines, 0), rd = ok.reduce((n, f) => n + f.readings, 0);
        set("extract", ok.length ? "done" : "failed", inv + " facturen · " + lines + " regels" + (rd ? " · " + rd + " meterstanden" : ""));
        if (!data.can_analyse || !ok.length) { finish(data, bad, null); return; }
        set("tariffs", "active");
        const fd = new FormData(); fd.append("csrf_token", csrf());
        fetch(up.dataset.analyse, { method: "POST", body: fd, headers: { Accept: "application/json" } })
          .then((r) => r.json()).then((res) => {
            set("tariffs", "done", res.rules + " controleregels");
            set("discrepancies", "done", res.findings_new + " nieuw · " + res.findings_total + " totaal");
            finish(data, bad, res);
          }).catch(() => { set("tariffs", "failed", "Analyse niet gelukt"); finish(data, bad, null); });
      });
      xhr.addEventListener("error", () => fail("De verbinding werd onderbroken. Controleer uw internetverbinding en probeer het opnieuw."));
      const fd = new FormData(up); xhr.send(fd);

      function fail(msg) {
        const active = steps.find((s) => s.className === "active"); if (active) { active.className = "failed"; $(".detail", active).textContent = ""; }
        summary.hidden = false; summary.className = "alert critical"; summary.innerHTML = "";
        summary.append(Object.assign(document.createElement("span"), { textContent: msg }));
        addRetry();
      }
      function finish(data, bad, res) {
        if (!res) { set("tariffs", data.can_analyse ? "failed" : "done", data.can_analyse ? "" : "door specialist"); set("discrepancies", "done", data.can_analyse ? "" : "na controle zichtbaar"); }
        summary.hidden = false; summary.innerHTML = "";
        summary.className = bad.length ? "alert review" : "alert positive";
        const t = document.createElement("div");
        const b = document.createElement("b");
        b.textContent = bad.length ? "Een deel van de documenten is niet verwerkt." : "Documenten verwerkt.";
        t.append(b);
        if (res && res.findings_new) t.append(document.createTextNode(" " + res.findings_new + " nieuwe bevinding(en) klaar voor beoordeling."));
        else if (!data.can_analyse) t.append(document.createTextNode(" Onze specialisten controleren de gegevens; u ziet bevestigde bevindingen in uw overzicht."));
        bad.forEach((f) => { const p = document.createElement("div"); p.className = "small"; p.textContent = f.name + ": " + f.message; t.append(p); });
        summary.append(t);
        const go = document.createElement("a"); go.className = "btn btn-sm btn-primary"; go.href = up.dataset.next || location.pathname; go.textContent = "Bekijk resultaat";
        summary.append(go);
        addRetry();
      }
      function addRetry() {
        const again = document.createElement("button"); again.type = "button"; again.className = "btn btn-sm"; again.textContent = "Nog een bestand uploaden";
        again.addEventListener("click", () => { panel.hidden = true; up.hidden = false; input.value = ""; showFiles(); $$("button.is-loading", up).forEach((x) => x.classList.remove("is-loading")); });
        summary.append(again);
      }
    });
  }

  /* ---------- live refresh (AI Operations): reload when the server state changes ---------- */
  const live = $("[data-pulse]");
  if (live && window.fetch) {
    const url = live.dataset.pulse, start = live.dataset.pulseV;
    let busy = false;
    setInterval(() => {
      if (busy || document.hidden || document.querySelector("dialog[open]") || document.activeElement?.matches("input, textarea, select")) return;
      busy = true;
      fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then((r) => r.json()).then((d) => { if (d.v !== start) location.reload(); })
        .catch(() => {}).finally(() => { busy = false; });
    }, 3000);
  }

  /* ---------- landing: invoice analysis demo ---------- */
  const demo = $(".demo");
  if (demo) {
    const amt = $("[data-demo-amount]", demo), target = parseFloat(amt.dataset.demoAmount);
    const final = () => { demo.classList.add("final"); amt.textContent = "€ " + nl.format(target); };
    if (reduce) { final(); }
    else {
      const run = () => {
        demo.classList.remove("final", "claimed"); void demo.offsetWidth; demo.classList.add("run");
        amt.textContent = "€ 0,00";
        setTimeout(() => {
          const start = performance.now();
          const step = (now) => { const p = Math.min(1, (now - start) / 900); amt.textContent = "€ " + nl.format(target * (1 - Math.pow(1 - p, 3))); if (p < 1) requestAnimationFrame(step); };
          requestAnimationFrame(step);
        }, 2800);
        setTimeout(() => demo.classList.add("claimed"), 4300);
        setTimeout(() => { demo.classList.remove("run"); final(); }, 5200);
      };
      const io = new IntersectionObserver((en) => { if (en[0].isIntersecting) { io.disconnect(); run(); } }, { threshold: .35 });
      io.observe(demo);
      const replay = $("[data-demo-replay]");
      if (replay) replay.addEventListener("click", run);
    }
  }
})();
