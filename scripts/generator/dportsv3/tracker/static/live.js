// Unified live-poll helper — one resilient polling loop for every live
// surface (job activity feed, runner status, verify wait, active builds).
//
// The page always renders a complete, correct snapshot server-side; dpLive
// only streams deltas on top of it. Callers supply url() and onData(data);
// the loop owns scheduling, pause-on-hidden, manual pause/resume, and stop.
// onData may return the string "stop" to end the loop (e.g. a job reached a
// terminal state). This replaces the per-page hand-rolled setTimeout/
// setInterval pollers so there is one cadence/backoff/lifecycle to reason
// about and test.
(function () {
  window.dpLive = function (opts) {
    var interval = opts.intervalMs || 3000;
    var stopped = false;
    var paused = false;
    var timer = null;

    function clear() {
      if (timer) { clearTimeout(timer); timer = null; }
    }
    function schedule(ms) {
      if (stopped) return;
      clear();
      timer = setTimeout(tick, ms == null ? interval : ms);
    }
    async function tick() {
      if (stopped) return;
      // Skip the fetch while the tab is hidden or the user paused, but keep
      // the loop alive so it resumes cleanly.
      if (paused || (opts.pauseWhenHidden !== false && document.hidden)) {
        schedule();
        return;
      }
      try {
        var resp = await fetch(opts.url(), { cache: "no-store" });
        if (resp.ok) {
          var data = await resp.json();
          if (opts.onData(data) === "stop") { stopped = true; clear(); return; }
        }
      } catch (e) {
        // transient network blip — just try again next tick
      }
      schedule();
    }

    var handle = {
      start: function (ms) { schedule(ms); return handle; },
      stop: function () { stopped = true; clear(); },
      pause: function () { paused = true; },
      resume: function () { paused = false; schedule(0); },
      isPaused: function () { return paused; },
      poke: function () { schedule(0); },
    };
    return handle;
  };
})();
