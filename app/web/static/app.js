/* Factuurspoor — progressive enhancement and the motion system's behaviour. Every page works without this file.
   See docs/motion-design-system.md for the rules this code follows. */
(() => {
  "use strict";
  const html = document.documentElement;
  html.classList.add("js");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const nl2 = new Intl.NumberFormat("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nl0 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 });
  const csrf = () => ($('meta[name="csrf-token"]') || {}).content || "";
  const store = {
    get(k) { try { return JSON.parse(sessionStorage.getItem(k)); } catch (_) { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } },
  };
  const easeOut = (p) => 1 - Math.pow(1 - p, 3);

  /* ======================================================================= numbers */
  const fmt = (v, kind) => (kind === "eur" ? "€ " + nl2.format(v) : nl0.format(v));
  function tween(el, from, to, kind, dur = 520) {
    if (reduce || from === to) { el.textContent = fmt(to, kind); return; }
    const start = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - start) / dur);
      el.textContent = fmt(from + (to - from) * easeOut(p), kind);
      if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(to, kind);
    };
    requestAnimationFrame(step);
  }
  /* A figure animates only when its value changed since this person last saw it (or during a live update). */
  function animateChanges(scope, previous) {
    $$("[data-count-key]", scope).forEach((el) => {
      const key = (el.hasAttribute("data-count-global") ? "" : location.pathname) + "|" + el.dataset.countKey;
      const now = parseFloat(el.dataset.value), kind = el.dataset.format || "int";
      if (!isFinite(now)) return;
      const before = previous && key in previous ? previous[key] : store.get("n" + key);
      store.set("n" + key, now);
      if (before === null || before === undefined || before === now) return;
      if (kind === "eur" || el.classList.contains("countup")) tween(el, before, now, kind);
      const flash = el.closest(".figure, .ops-metric, .pipe li, .kpi-row td, .f-row") || el;
      flash.classList.remove("changed", "up", "down"); void flash.offsetWidth;
      flash.classList.add("changed", now > before ? "up" : "down");
      if (el.classList.contains("count")) { el.classList.remove("changed"); void el.offsetWidth; el.classList.add("changed"); }
    });
  }
  function snapshotNumbers(scope) {
    const out = {};
    $$("[data-count-key]", scope).forEach((el) => {
      out[(el.hasAttribute("data-count-global") ? "" : location.pathname) + "|" + el.dataset.countKey] = parseFloat(el.dataset.value);
    });
    return out;
  }
  function snapshotChips(scope) {
    const out = {};
    $$("[data-state-key]", scope).forEach((el) => { out[el.dataset.stateKey] = el.textContent.trim(); });
    return out;
  }

  /* ======================================================================= enhance: run on load and after live updates */
  function enhance(scope, soft) {
    /* toasts */
    $$(".toast", scope).forEach((t) => { if (!t.classList.contains("error")) setTimeout(() => dismissToast(t), 6000); });

    /* hero figure count-up on first view (only when it is not a live update) */
    if (!reduce && !soft && "IntersectionObserver" in window) {
      const io = new IntersectionObserver((entries) => entries.forEach((en) => {
        if (!en.isIntersecting) return; io.unobserve(en.target);
        const el = en.target, target = parseFloat(el.dataset.countup);
        if (isFinite(target) && target !== 0) tween(el, 0, target, "eur", 480);
      }), { threshold: .4 });
      $$("[data-countup]", scope).forEach((el) => io.observe(el));
    }

    /* drawing (charts, timelines, lifecycles) and reveals: held until they scroll into view */
    if (!soft && "IntersectionObserver" in window) {
      const vh = window.innerHeight;
      const drawables = $$("svg.chart, svg.fbar, svg.pbar, svg.hbar, .bars, .score, .spoor, .pipeline, [data-reveal], [data-story-reveal]", scope);
      const io = new IntersectionObserver((entries) => entries.forEach((en) => {
        if (!en.isIntersecting) return; io.unobserve(en.target);
        en.target.classList.remove("will-draw"); en.target.classList.add("is-in");
      }), { threshold: .2, rootMargin: "0px 0px -6% 0px" });
      drawables.forEach((el) => {
        if (el.getBoundingClientRect().top > vh * .92) { el.classList.add("will-draw"); io.observe(el); }
        else el.classList.add("is-in");
      });
      $$("[data-reveal-group]", scope).forEach((g) => $$("[data-reveal]", g).forEach((el, i) => el.style.setProperty("--i", String(Math.min(i, 8)))));
    } else {
      $$("[data-reveal], [data-story-reveal]", scope).forEach((el) => el.classList.add("is-in"));
    }

    /* document sheets: skeleton until the page image has loaded */
    $$(".sheet img", scope).forEach((img) => {
      const sheet = img.closest(".sheet");
      const done = () => sheet.classList.remove("skeleton");
      if (img.complete && img.naturalWidth) done();
      else {
        img.addEventListener("load", done);
        img.addEventListener("error", () => {
          sheet.classList.remove("skeleton");
          sheet.innerHTML = '<p class="source-empty">Deze pagina kon niet worden weergegeven. Download het origineel om het te bekijken.</p>';
        });
      }
    });

    /* filters that are set look set */
    $$(".filters select, .filters input", scope).forEach((f) => { if (f.type !== "hidden" && f.value) f.classList.add("is-set"); });
  }

  /* ======================================================================= delegated interactions */
  function dismissToast(t) { if (!t.isConnected || t.classList.contains("leaving")) return; t.classList.add("leaving"); setTimeout(() => t.remove(), 240); }

  function openDialog(b) {
    const d = document.getElementById(b.dataset.dialogOpen);
    if (!d || typeof d.showModal !== "function") return false;
    const act = b.dataset.action; const input = act && $('input[name="action"]', d);
    if (input) input.value = act;
    const form = $("form", d); if (form && b.dataset.url) form.action = b.dataset.url;
    const title = $("[data-dialog-title]", d); if (title && b.dataset.title) title.textContent = b.dataset.title;
    d.classList.remove("closing"); d.showModal();
    const f = $("textarea, input:not([type=hidden])", d); f && f.focus();
    return true;
  }
  function closeDialog(d) {
    if (!d.open || d.classList.contains("closing")) return;
    if (reduce) { d.close(); return; }
    d.classList.add("closing");
    setTimeout(() => { d.classList.remove("closing"); d.close(); }, 140);
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    const opener = t.closest("[data-dialog-open]");
    if (opener && openDialog(opener)) { e.preventDefault(); return; }
    const closer = t.closest("[data-dialog-close]");
    if (closer) { closeDialog(closer.closest("dialog")); return; }
    if (t.tagName === "DIALOG" && t.open) { /* click on the backdrop */
      const r = t.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeDialog(t);
      return;
    }
    const toastClose = t.closest(".toast .close");
    if (toastClose) { dismissToast(toastClose.closest(".toast")); return; }
    /* navigation feedback for ordinary same-origin links */
    const a = t.closest("a[href]");
    if (a && !e.defaultPrevented && e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !a.target && !a.hasAttribute("download")) {
      const url = new URL(a.href, location.href);
      if (url.origin === location.origin && !(url.pathname === location.pathname && url.hash) && !/\.(zip|pdf|png|json)$/.test(url.pathname)) navStart();
    }
  });
  document.addEventListener("cancel", (e) => { if (e.target.tagName === "DIALOG") { e.preventDefault(); closeDialog(e.target); } }, true);

  document.addEventListener("submit", (e) => {
    const f = e.target, b = e.submitter;
    if (b && b.classList.contains("btn") && !f.hasAttribute("data-no-loading")) setTimeout(() => b.classList.add("is-loading"), 0);
    if (!e.defaultPrevented && !f.hasAttribute("data-upload") && (f.method || "").toLowerCase() !== "dialog") navStart();
  });
  document.addEventListener("change", (e) => {
    const f = e.target;
    if (f.closest && f.closest(".filters")) f.classList.toggle("is-set", !!f.value);
  });

  /* ======================================================================= navigation progress */
  let bar;
  function navStart() {
    if (!bar) { bar = document.createElement("div"); bar.className = "nav-progress"; bar.setAttribute("aria-hidden", "true"); document.body.appendChild(bar); }
    bar.className = "nav-progress"; void bar.offsetWidth; bar.classList.add("go");
  }
  window.addEventListener("pageshow", () => { if (bar) bar.className = "nav-progress"; $$(".btn.is-loading").forEach((b) => b.classList.remove("is-loading")); });

  /* ======================================================================= mobile navigation drawer */
  const side = $(".sidebar"), menuBtn = $("[data-menu]");
  if (side && menuBtn) {
    let scrim;
    const close = () => {
      side.classList.remove("open"); menuBtn.setAttribute("aria-expanded", "false");
      if (scrim) { const s = scrim; scrim = null; s.classList.remove("show"); setTimeout(() => s.remove(), 220); }
    };
    menuBtn.addEventListener("click", () => {
      if (side.classList.contains("open")) return close();
      scrim = document.createElement("div"); scrim.className = "scrim"; scrim.addEventListener("click", close);
      document.body.appendChild(scrim); void scrim.offsetWidth; scrim.classList.add("show");
      side.classList.add("open"); menuBtn.setAttribute("aria-expanded", "true");
      const first = $(".nav a", side); first && first.focus({ preventScroll: true });
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && side.classList.contains("open")) close(); });
  }

  /* public header border on scroll */
  const header = $(".site-header");
  if (header) {
    const onScroll = () => header.classList.toggle("scrolled", window.scrollY > 8);
    onScroll(); window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ======================================================================= split view: evidence ↔ highlighted region */
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

  /* ======================================================================= upload with a visible processing pipeline */
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
      const steps = $$("li[data-step]", panel), progress = $(".progress rect", panel), summary = $("[data-summary]", panel);
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
        if (ev.lengthComputable) { const pct = Math.round(ev.loaded / ev.total * 100); progress.setAttribute("width", pct + "%"); set("upload", "active", pct + "%"); }
      });
      xhr.upload.addEventListener("load", () => { set("upload", "done", ""); set("read", "active"); progress.setAttribute("width", "100%"); });
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
      xhr.send(new FormData(up));

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
        const t = document.createElement("div"), b = document.createElement("b");
        b.textContent = bad.length ? "Een deel van de documenten is niet verwerkt." : "Documenten verwerkt.";
        t.append(b);
        if (res && res.findings_total) t.append(document.createTextNode(" " + res.findings_total + " bevinding(en), klaar voor beoordeling."));
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

  /* ======================================================================= live updates without a page reload */
  /* When the server state changes, the new page is fetched and swapped in place: no flash, no scroll jump,
     no entrance animations; only what changed animates (numbers count, changed statuses get a brief tint). */
  const main = $("#main");
  let live = $("[data-pulse]");
  if (live && main && window.fetch && window.DOMParser) {
    let version = live.dataset.pulseV, busy = false;
    const tick = () => {
      live = $("[data-pulse]");
      if (!live || busy || document.hidden || document.querySelector("dialog[open]") || document.activeElement?.matches("input, textarea, select")) return;
      busy = true;
      fetch(live.dataset.pulse, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then((r) => r.json()).then((d) => { if (d.v !== version) return swap().then(() => { version = d.v; }); })
        .catch(() => {}).finally(() => { busy = false; });
    };
    const swap = () => fetch(location.href, { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then((r) => { if (!r.ok || r.redirected) throw new Error("reload"); return r.text(); })
      .then((text) => {
        const doc = new DOMParser().parseFromString(text, "text/html");
        const nextMain = doc.getElementById("main");
        if (!nextMain) throw new Error("reload");
        const numbers = snapshotNumbers(document), chips = snapshotChips(main);
        const scrolls = $$(".feed-scroll, .logstream.scroll").map((el) => el.scrollTop);
        html.classList.add("no-enter");
        main.innerHTML = nextMain.innerHTML;
        const nextNav = doc.querySelector(".sidebar .nav"), nav = $(".sidebar .nav");
        if (nextNav && nav) nav.innerHTML = nextNav.innerHTML;
        $$(".feed-scroll, .logstream.scroll").forEach((el, i) => { if (scrolls[i]) el.scrollTop = scrolls[i]; });
        enhance(main, true);
        animateChanges(document, numbers);
        $$("[data-state-key]", main).forEach((el) => {
          if (el.dataset.stateKey in chips && chips[el.dataset.stateKey] !== el.textContent.trim()) { el.classList.add("changed"); }
        });
      })
      .catch(() => { location.reload(); });
    setInterval(tick, 3000);
  }

  /* ======================================================================= landing: product demo */
  const demo = $("[data-demo]");
  if (demo) {
    const steps = 10, amount = $("[data-demo-amount]", demo), target = parseFloat(amount.dataset.demoAmount);
    const timeline = [0, 500, 1300, 2100, 2900, 3700, 4400, 5000, 5900, 6700]; // ms per step
    let timers = [];
    const setStep = (n) => {
      for (let i = 1; i <= steps; i++) demo.classList.toggle("s" + i, i <= n);
      demo.dataset.step = String(n);
      const label = $("[data-demo-status]", demo);
      if (label) label.textContent = (demo.querySelector(`[data-step-label="${n}"]`) || {}).textContent || "";
    };
    const finalState = () => { timers.forEach(clearTimeout); timers = []; setStep(steps); amount.textContent = "€ " + nl2.format(target); demo.classList.add("done"); };
    const run = () => {
      timers.forEach(clearTimeout); timers = [];
      demo.classList.remove("done"); setStep(0); amount.textContent = "€ 0,00";
      timeline.forEach((ms, i) => timers.push(setTimeout(() => {
        setStep(i + 1);
        if (i + 1 === 8) tween(amount, 0, target, "eur", 900);
        if (i + 1 === steps) demo.classList.add("done");
      }, ms)));
    };
    if (reduce || !("IntersectionObserver" in window)) finalState();
    else {
      setStep(0);
      const io = new IntersectionObserver((en) => { if (en[0].isIntersecting) { io.disconnect(); run(); } }, { threshold: .3 });
      io.observe(demo);
    }
    const replay = $("[data-demo-replay]");
    if (replay) replay.addEventListener("click", () => (reduce ? finalState() : run()));
    /* interactive extraction: field ↔ region on the invoice, with value, confidence and location */
    const detail = $("[data-field-detail]", demo);
    $$("[data-field]", demo).forEach((f) => {
      const region = $(`[data-region="${f.dataset.field}"]`, demo);
      const on = () => {
        $$(".lit", demo).forEach((x) => x.classList.remove("lit"));
        f.classList.add("lit"); region && region.classList.add("lit");
        if (detail) { detail.hidden = false; $("[data-d-value]", detail).textContent = f.dataset.value;
          $("[data-d-conf]", detail).textContent = f.dataset.conf; $("[data-d-loc]", detail).textContent = f.dataset.loc; }
      };
      const off = () => { f.classList.remove("lit"); region && region.classList.remove("lit"); };
      f.addEventListener("mouseenter", on); f.addEventListener("focus", on);
      f.addEventListener("mouseleave", off); f.addEventListener("blur", off);
    });
  }

  /* ======================================================================= landing: scroll story */
  const story = $("[data-story]");
  if (story) {
    const stage = $("[data-story-stage]", story), chapters = $$("[data-chapter]", story);
    const setChapter = (n) => {
      for (let i = 1; i <= chapters.length; i++) stage.classList.toggle("c" + i, i <= n);
      chapters.forEach((c) => c.classList.toggle("current", Number(c.dataset.chapter) === n));
      const amt = $("[data-story-amount]", stage);
      if (amt && n >= 6 && !amt.dataset.done) { amt.dataset.done = "1"; tween(amt, 0, parseFloat(amt.dataset.storyAmount), "eur", 900); }
    };
    /* Small screens get the finished picture: no sticky stage, the chapters read as plain text. */
    if (reduce || !("IntersectionObserver" in window) || window.matchMedia("(max-width: 900px)").matches) setChapter(chapters.length);
    else {
      let current = 1;
      setChapter(1);
      const io = new IntersectionObserver((entries) => entries.forEach((en) => {
        if (en.isIntersecting) { const n = Number(en.target.dataset.chapter); if (n !== current) { current = n; setChapter(n); } }
      }), { rootMargin: "-45% 0px -45% 0px" });
      chapters.forEach((c) => io.observe(c));
    }
  }

  /* ======================================================================= start */
  enhance(document, false);
  animateChanges(document, null);
  html.setAttribute("data-js-ready", "");
})();
