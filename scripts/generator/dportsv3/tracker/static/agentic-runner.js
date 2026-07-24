// Runner-status page: live-refresh the status cells in place via the shared
// dpLive poller. The initial values render server-side; this only updates
// them. [pause]/[resume] toggles the loop.
(function () {
  var indicator = document.getElementById("live-indicator");
  var toggle = document.getElementById("live-toggle");
  var statusText = indicator ? indicator.querySelector(".status-text") : null;

  function setText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value == null ? "—" : value;
  }
  function setJob(jobId) {
    var el = document.getElementById("runner-job");
    if (!el) return;
    if (jobId) {
      el.innerHTML = '<a href="/agentic/jobs/' + encodeURIComponent(jobId)
        + '">' + jobId + "</a>";
    } else {
      el.textContent = "—";
    }
  }

  var poller = window.dpLive({
    intervalMs: 4000,
    url: function () { return "/api/runner-status"; },
    onData: function (d) {
      setText("runner-status", d.status);
      setJob(d.job_id);
      setText("runner-stage", d.current_stage);
      setText("runner-started", d.started_at);
      setText("runner-updated", d.updated_at);
      var extra = document.getElementById("runner-extra");
      if (extra && d.extra_json) extra.textContent = d.extra_json;
    },
  });

  if (toggle) {
    toggle.addEventListener("click", function () {
      if (poller.isPaused()) {
        poller.resume();
        if (indicator) indicator.classList.add("active");
        if (statusText) statusText.textContent = "live";
        toggle.textContent = "[pause]";
      } else {
        poller.pause();
        if (indicator) indicator.classList.remove("active");
        if (statusText) statusText.textContent = "paused";
        toggle.textContent = "[resume]";
      }
    });
  }

  poller.start(4000);
})();
