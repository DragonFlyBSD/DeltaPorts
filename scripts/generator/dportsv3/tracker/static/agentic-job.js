// Job-detail page behaviour: live activity refresh + client-side
// column sort. Both self-guard on the elements/state they need, so the
// file is a no-op on idle jobs or jobs without an activity table.

// --- Live activity refresh (active jobs only) ---
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
  var pollMs = 3000;
  var paused = false;
  var timer = null;

  // States that terminate polling.
  var TERMINAL = ["done", "dead", "escalated"];

  function fmtAgo(ts) {
    var seconds = Math.round((Date.now() - ts) / 1000);
    if (seconds < 5) return "just now";
    if (seconds < 60) return seconds + "s ago";
    var m = Math.floor(seconds / 60);
    return m + "m ago";
  }

  // Escape a string for safe innerHTML insertion. Used everywhere
  // user-content goes into innerHTML (messages, error fields,
  // stderr_tail, etc.) — without this, an apostrophe in an error
  // string could break the page or worse.
  function esc(s) {
    if (s == null) return "";
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  // Stage → class lookup mirrors _activity_row.html's row_class
  // logic. CSS for these classes lives in this file's <style>
  // block so server-rendered and JS-rendered rows match.
  function rowClassFor(stage) {
    if (stage === "attempt_start") return "activity-attempt-start";
    if (stage === "attempt_end")   return "activity-attempt-end";
    if (stage === "decision")      return "activity-decision";
    if (stage && stage.indexOf("tool:") === 0) return "activity-tool";
    return "";
  }

  // Build the "extra info" markup that lands inside the message
  // column for non-llm_turn rows. Mirrors the is_decision and
  // is_tool-and-not-ok blocks in _activity_row.html so live-
  // appended decision rows and failed-tool rows look the same as
  // their server-rendered counterparts (the user-visible
  // "show diagnostic" toggle in particular).
  function extraInfoHTML(stage, extra) {
    var parts = [];
    if (stage === "decision" && extra && typeof extra === "object") {
      var meta =
        "action=" + esc(extra.action || "—") +
        " · tier=" + esc(extra.tier || "—");
      if (extra.classification) meta += " · class=" + esc(extra.classification);
      if (extra.confidence) meta += " · confidence=" + esc(extra.confidence);
      if (extra.recent_failures !== undefined) {
        meta += " · failures=" + esc(extra.recent_failures)
              + "/" + esc(extra.max_attempts || "?");
      }
      if (extra.original_tier) meta += " · original=" + esc(extra.original_tier);
      parts.push('<div class="decision-meta">' + meta + "</div>");
    }
    if (stage && stage.indexOf("tool:") === 0
        && extra && extra.ok === false
        && (extra.stderr_tail || extra.stdout_tail || extra.error)) {
      var diag = '<details class="tool-diagnostic" style="margin-top:4px;font-size:11px;">';
      diag += "<summary>show diagnostic";
      if (extra.rc !== undefined) diag += " (rc=" + esc(extra.rc) + ")";
      diag += "</summary>";
      if (extra.error) {
        diag += '<div class="tool-error">error: ' + esc(extra.error) + "</div>";
      }
      if (extra.stderr_tail) {
        diag += "<pre>" + esc(extra.stderr_tail) + "</pre>";
      } else if (extra.stdout_tail) {
        diag += "<pre>" + esc(extra.stdout_tail) + "</pre>";
      }
      diag += "</details>";
      parts.push(diag);
    }
    // Non-tool failure rows (convert_verify_failed,
    // commit_port_changes_failed, patch_preflight_*, etc.) carry
    // their diagnostic in extra.diag_tail / stderr_tail / stdout_tail.
    // Mirror the server-side _activity_row.html block.
    var isTool = stage && stage.indexOf("tool:") === 0;
    var isLLMTurn = stage === "llm_turn";
    if (!isTool && !isLLMTurn && extra
        && (extra.diag_tail || extra.stderr_tail
            || extra.stdout_tail || extra.error)) {
      var diag2 = '<details class="tool-diagnostic" style="margin-top:4px;font-size:11px;">';
      diag2 += "<summary>show diagnostic";
      if (extra.rc !== undefined) diag2 += " (rc=" + esc(extra.rc) + ")";
      diag2 += "</summary>";
      if (extra.error) {
        diag2 += '<div class="tool-error">error: ' + esc(extra.error) + "</div>";
      }
      if (extra.diag_tail) {
        diag2 += "<pre>" + esc(extra.diag_tail) + "</pre>";
      } else if (extra.stderr_tail) {
        diag2 += "<pre>" + esc(extra.stderr_tail) + "</pre>";
      } else if (extra.stdout_tail) {
        diag2 += "<pre>" + esc(extra.stdout_tail) + "</pre>";
      }
      diag2 += "</details>";
      parts.push(diag2);
    }
    return parts.join("");
  }

  function renderRow(a) {
    // Mirror _activity_row.html shape. Server-side is canonical;
    // this is the live-prepend fallback for new rows.
    var tr = document.createElement("tr");
    var stage = a.stage || "—";
    var extra = a.extra || {};
    var isLLMTurn = stage === "llm_turn";
    // Per-stage CSS class drives the row's background / font.
    // "new-row" stays as an additional class so live-fade
    // animations (if any are added later) can target it.
    tr.className = ("new-row " + rowClassFor(stage)).trim();
    var p = isLLMTurn ? (extra.prompt_tokens || 0).toLocaleString() : "";
    var c = isLLMTurn ? (extra.completion_tokens || 0).toLocaleString() : "";
    var t = isLLMTurn ? (extra.total_tokens || 0).toLocaleString() : "";
    // Cum column = cumulative billable; pre-H4 rows fall back to total.
    var cumBillable = (extra.cumulative_billable_tokens != null)
      ? extra.cumulative_billable_tokens
      : (extra.cumulative_total_tokens || 0);
    var cum = isLLMTurn ? cumBillable.toLocaleString() : "";
    var messageCell;
    if (isLLMTurn) {
      messageCell = (extra.tools_requested && extra.tools_requested.length)
                      ? "→ " + extra.tools_requested.map(esc).join(", ")
                      : (extra.text_only
                          ? "(text-only final response)"
                          : esc(a.message || "—"));
    } else {
      messageCell = esc(a.message || "—") + extraInfoHTML(stage, extra);
    }
    tr.innerHTML =
      '<td style="white-space:nowrap;">' + esc(a.ts || "") + "</td>" +
      "<td>" + esc(stage) + "</td>" +
      "<td>" + (a.duration_ms == null ? "—" : esc(a.duration_ms)) + "</td>" +
      '<td class="tok' + (isLLMTurn ? "" : " tok-empty") + '">' + (p || "·") + "</td>" +
      '<td class="tok' + (isLLMTurn ? "" : " tok-empty") + '">' + (c || "·") + "</td>" +
      '<td class="tok' + (isLLMTurn ? "" : " tok-empty") + '">' + (t || "·") + "</td>" +
      '<td class="tok' + (isLLMTurn ? "" : " tok-empty") + '">' + (cum || "·") + "</td>" +
      '<td' + (isLLMTurn ? ' class="tool-list"' : "") + ">" + messageCell + "</td>";
    return tr;
  }

  async function poll() {
    if (document.hidden || paused) {
      schedule();
      return;
    }
    try {
      var url = "/api/activity?job_id=" + encodeURIComponent(jobId)
                + "&since_id=" + sinceId + "&limit=200";
      if (stageFilter) {
        url += "&stage_filter=" + encodeURIComponent(stageFilter);
      }
      var resp = await fetch(url);
      if (resp.ok) {
        var rows = await resp.json();
        if (rows && rows.length) {
          // Server returns oldest-first when since_id > 0; the
          // activity table is newest-first, so we prepend each
          // in reverse to make the newest land at the top.
          rows.forEach(function (a) {
            if (a.id && a.id > sinceId) sinceId = a.id;
            tbody.insertBefore(renderRow(a), tbody.firstChild);
          });
          if (countEl) {
            var current = tbody.querySelectorAll("tr").length;
            countEl.textContent = "(" + current + " rows, " + rows.length + " new)";
          }
        }
        if (lastUpdateEl) lastUpdateEl.textContent = fmtAgo(Date.now());
      }
    } catch (err) { /* network blip, try again next tick */ }
    // Re-fetch the job state to detect terminal transitions.
    try {
      var jobResp = await fetch("/api/jobs/" + encodeURIComponent(jobId));
      if (jobResp.ok) {
        var job = await jobResp.json();
        if (TERMINAL.indexOf(job.state) >= 0) {
          indicator.classList.remove("active");
          if (statusText) statusText.textContent = "idle (" + job.state + ")";
          if (pauseLink) pauseLink.style.display = "none";
          return;  // stop polling
        }
      }
    } catch (err) { /* swallow */ }
    schedule();
  }

  function schedule() { timer = setTimeout(poll, pollMs); }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && statusText) statusText.textContent = "live";
  });

  if (pauseLink) {
    pauseLink.addEventListener("click", function (ev) {
      ev.preventDefault();
      paused = !paused;
      pauseLink.textContent = paused ? "[resume]" : "[pause]";
      if (statusText) statusText.textContent = paused ? "paused" : "live";
      if (!paused) poll();
    });
  }

  // Kick off the loop after a short delay so initial render
  // finishes first.
  schedule();
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
