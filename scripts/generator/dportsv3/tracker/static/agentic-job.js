// Job-detail page behaviour: live activity refresh + client-side
// column sort. Both self-guard on the elements/state they need, so the
// file is a no-op on idle jobs or jobs without an activity table.

// --- Live activity refresh (active jobs only) ---
// Streams new activity rows as SERVER-RENDERED fragments (one render path,
// shared with the initial page render via _activity_row.html) and lets the
// shared dpLive helper own the poll loop / pause / stop.
(function () {
  var indicator = document.getElementById("live-indicator");
  if (!indicator) return;
  if (!indicator.classList.contains("active")) return;
  var jobId = indicator.dataset.jobId;
  var sinceId = parseInt(indicator.dataset.sinceId || "0", 10);
  var stageFilter = indicator.dataset.stageFilter || "";
  var tbody = document.getElementById("activity-tbody");
  var countEl = document.getElementById("activity-count");
  var lastUpdateEl = indicator.querySelector(".last-update");
  var statusText = indicator.querySelector(".status-text");
  var pauseLink = document.getElementById("pause-toggle");
  var TERMINAL = ["done", "dead", "escalated"];

  function fmtAgo(ts) {
    var seconds = Math.round((Date.now() - ts) / 1000);
    if (seconds < 5) return "just now";
    if (seconds < 60) return seconds + "s ago";
    return Math.floor(seconds / 60) + "m ago";
  }

  var poller = window.dpLive({
    intervalMs: 3000,
    url: function () {
      var u = "/api/jobs/" + encodeURIComponent(jobId)
            + "/activity-fragment?since_id=" + sinceId;
      if (stageFilter) u += "&stage_filter=" + encodeURIComponent(stageFilter);
      return u;
    },
    onData: function (data) {
      if (data.html) {
        // Rows arrive oldest-first; inserting each at the top makes the
        // newest land highest, matching the newest-first static table.
        var frag = document.createElement("tbody");
        frag.innerHTML = data.html;
        Array.prototype.forEach.call(frag.querySelectorAll("tr"), function (tr) {
          tr.classList.add("new-row");
          tbody.insertBefore(tr, tbody.firstChild);
        });
        if (data.since_id) sinceId = data.since_id;
        if (countEl && data.count) {
          countEl.textContent = "(" + tbody.querySelectorAll("tr").length
            + " rows, " + data.count + " new)";
        }
      }
      if (lastUpdateEl) lastUpdateEl.textContent = fmtAgo(Date.now());
      if (TERMINAL.indexOf(data.job_state) >= 0) {
        indicator.classList.remove("active");
        if (statusText) statusText.textContent = "idle (" + data.job_state + ")";
        if (pauseLink) pauseLink.style.display = "none";
        return "stop";
      }
    },
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && statusText && !poller.isPaused()) {
      statusText.textContent = "live";
    }
  });

  if (pauseLink) {
    pauseLink.addEventListener("click", function (ev) {
      ev.preventDefault();
      if (poller.isPaused()) {
        poller.resume();
        pauseLink.textContent = "[pause]";
        if (statusText) statusText.textContent = "live";
      } else {
        poller.pause();
        pauseLink.textContent = "[resume]";
        if (statusText) statusText.textContent = "paused";
      }
    });
  }

  poller.start(3000);
})();

// --- Client-side column sort ---
(function () {
  var table = document.getElementById("activity-table");
  if (!table) return;
  var tbody = document.getElementById("activity-tbody");
  var sortLabel = document.getElementById("sort-label");
  var sortReset = document.getElementById("sort-reset");
  var headers = table.querySelectorAll("th.sortable");

  // Snapshot the initial row order so [reset] can restore it.
  var originalOrder = Array.from(tbody.querySelectorAll("tr"));

  var current = { key: null, dir: 0 };  // dir: 0=none, 1=desc, -1=asc

  function applySort(key, dir) {
    var rows = Array.from(tbody.querySelectorAll("tr"));
    rows.sort(function (a, b) {
      var av = parseInt(a.dataset["sort" + key.charAt(0).toUpperCase() + key.slice(1)] || "0", 10);
      var bv = parseInt(b.dataset["sort" + key.charAt(0).toUpperCase() + key.slice(1)] || "0", 10);
      return dir === 1 ? bv - av : av - bv;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
    headers.forEach(function (h) {
      h.classList.remove("sort-asc", "sort-desc");
      if (h.dataset.sort === key) {
        h.classList.add(dir === 1 ? "sort-desc" : "sort-asc");
      }
    });
    if (sortLabel) sortLabel.textContent = key + " " + (dir === 1 ? "↓" : "↑");
    if (sortReset) sortReset.style.display = "inline";
  }

  function reset() {
    originalOrder.forEach(function (r) { tbody.appendChild(r); });
    headers.forEach(function (h) {
      h.classList.remove("sort-asc", "sort-desc");
    });
    current = { key: null, dir: 0 };
    if (sortLabel) sortLabel.textContent = "chronological";
    if (sortReset) sortReset.style.display = "none";
  }

  headers.forEach(function (h) {
    h.addEventListener("click", function () {
      var key = h.dataset.sort;
      if (current.key !== key) {
        current = { key: key, dir: 1 };       // first click = desc
      } else if (current.dir === 1) {
        current = { key: key, dir: -1 };       // second = asc
      } else {
        reset();
        return;
      }
      applySort(current.key, current.dir);
    });
  });

  if (sortReset) {
    sortReset.addEventListener("click", function (ev) {
      ev.preventDefault();
      reset();
    });
  }
})();

// --- Abandon job (mark dead) ---
(function () {
  var btn = document.getElementById("abandon-btn");
  if (!btn) return;
  var flash = document.getElementById("abandon-flash");
  btn.addEventListener("click", async function () {
    var msg = "Abandon job " + btn.dataset.jobId + " (state="
      + btn.dataset.state + ")? It will be marked dead with "
      + "retire_reason='abandoned' and never picked up again.";
    if (!confirm(msg)) return;
    btn.disabled = true;
    try {
      var resp = await fetch(
        "/api/jobs/" + encodeURIComponent(btn.dataset.jobId) + "/abandon",
        {method: "POST", headers: {"Content-Type": "application/json"}}
      );
      var data = await resp.json().catch(function () { return {}; });
      if (resp.ok) {
        flash.textContent = "Abandoned. Refreshing…";
        flash.style.color = "green";
        setTimeout(function () { window.location.reload(); }, 700);
      } else {
        flash.textContent = (data.detail || ("HTTP " + resp.status));
        flash.style.color = "var(--c-fail, #c00)";
        btn.disabled = false;
      }
    } catch (err) {
      flash.textContent = "Network error: " + err;
      flash.style.color = "var(--c-fail, #c00)";
      btn.disabled = false;
    }
  });
})();
