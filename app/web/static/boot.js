/* Runs before first paint (blocking, tiny): lets CSS hold animated elements at their start state without a flash,
   and suppresses entrance motion right after a live update. Everything else lives in app.js. */
(function () {
  var h = document.documentElement;
  h.className += " js";
  // Safety net: if app.js did not run (blocked, cached old copy, script error), show everything in its final state
  // instead of leaving elements held back by the "js" class. app.js marks the page ready when it has started.
  window.addEventListener("load", function () {
    if (!h.hasAttribute("data-js-ready")) { h.className = h.className.replace(/(^|\s)js(\s|$)/g, " "); }
  });
  try { if (sessionStorage.getItem("fs-soft")) { sessionStorage.removeItem("fs-soft"); h.className += " no-enter"; } } catch (e) { /* private mode */ }
})();
