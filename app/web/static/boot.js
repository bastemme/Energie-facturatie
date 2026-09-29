/* Runs before first paint (blocking, tiny): lets CSS hold animated elements at their start state without a flash,
   and suppresses entrance motion right after a live update. Everything else lives in app.js. */
(function () {
  var h = document.documentElement;
  h.className += " js";
  try { if (sessionStorage.getItem("fs-soft")) { sessionStorage.removeItem("fs-soft"); h.className += " no-enter"; } } catch (e) { /* private mode */ }
})();
