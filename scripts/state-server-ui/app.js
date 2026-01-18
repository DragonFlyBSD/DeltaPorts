/*
 * Agentic DeltaPorts UI
 * Vanilla JS Bootstrap dashboard for scripts/state-server
 */

(() => {
  'use strict';

  // =============================================================================
  // Constants
  // =============================================================================

  const MAX_EVENTS = 5000;
  const STORAGE_KEYS = {
    lastEventId: 'dp.lastEventId',
    theme: 'dp.theme',
    paused: 'dp.paused',
  };

  const ROUTES = {
    overview: '#/overview',
    events: '#/events',
    jobs: '#/jobs',
    runs: '#/runs',
    ports: '#/ports',
  };

  const JOB_BADGES = {
    pending: 'secondary',
    inflight: 'primary',
    done: 'success',
    failed: 'danger',
  };

  const EVENT_BADGES = {
    job_failed: 'danger',
    pr_created: 'success',
    triage_written: 'warning',
    patch_written: 'warning',
    bundle_created: 'info',
    bundle_upserted: 'info',
    artifact_put: 'secondary',
    job_enqueued: 'secondary',
    job_claimed: 'primary',
    job_done: 'success',
    run_started: 'info',
    activity: 'info',
    runner_status: 'primary',
    user_context_updated: 'info',
  };

  // Runner status display config
  const RUNNER_STATUS_BADGES = {
    idle: 'secondary',
    processing: 'primary',
    stopped: 'danger',
    unknown: 'secondary',
    stale: 'warning',
  };

  // Files to hide from artifact display (internal/noisy files)
  const HIDDEN_ARTIFACTS = [
    'analysis/session_id.txt',
    'analysis/patch_session_id.txt',
  ];

  // =============================================================================
  // State
  // =============================================================================

  const state = {
    connection: { status: 'disconnected', lastEventId: null },
    events: [], // ring buffer
    jobsById: {},
    bundlesById: {},
    runsById: {},
    filters: { eventType: null, origin: '', onlyFailures: false },
    paused: false,
    selected: { kind: null, id: null },
    sse: { es: null, backoffMs: 500, backoffMaxMs: 30000, connectTimer: null },
    caches: {
      portsIndex: null, // { origins: string[], lastBuiltAt: number }
      activityLog: null, // [{ id, ts, job_id, stage, message, duration_ms, extra_json }]
      runnerStatus: null, // { status, job_id, current_stage, started_at, updated_at, is_stale }
    },
  };

  // =============================================================================
  // DOM helpers
  // =============================================================================

  function $(sel, root = document) {
    return root.querySelector(sel);
  }

  function $all(sel, root = document) {
    return Array.from(root.querySelectorAll(sel));
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function formatTs(ts) {
    if (!ts) return '-';
    // server emits ISO format; fall back if parse fails
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return ts;
    return d.toLocaleString();
  }

  function shortId(id, len = 10) {
    if (!id) return '-';
    const s = String(id);
    if (s.length <= len) return s;
    return s.slice(0, len) + '…';
  }

  function badge(text, bsColor, extra = '') {
    return `<span class="badge rounded-pill text-bg-${bsColor} ${extra}">${escapeHtml(text)}</span>`;
  }

  function mono(text) {
    return `<span class="font-monospace">${escapeHtml(text)}</span>`;
  }

  function icon(name) {
    return `<i class="bi bi-${name}"></i>`;
  }

  // =============================================================================
  // Toasts
  // =============================================================================

  function toast(title, body, opts = {}) {
    const container = $('#toast-container');
    if (!container) return;

    const id = `toast-${Math.random().toString(16).slice(2)}`;
    const klass = opts.className || '';
    const headerIcon = opts.icon ? `<i class="bi bi-${opts.icon} me-2"></i>` : '';

    const html = `
      <div id="${id}" class="toast ${klass}" role="alert" aria-live="assertive" aria-atomic="true">
        <div class="toast-header">
          ${headerIcon}
          <strong class="me-auto">${escapeHtml(title)}</strong>
          <small class="text-body-secondary">${escapeHtml(opts.small ?? '')}</small>
          <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">${body}</div>
      </div>
    `;

    container.insertAdjacentHTML('beforeend', html);
    const el = document.getElementById(id);
    const t = bootstrap.Toast.getOrCreateInstance(el, { delay: opts.delay ?? 6000 });
    el.addEventListener('hidden.bs.toast', () => el.remove());
    t.show();
  }

  // =============================================================================
  // API Client
  // =============================================================================

  async function fetchJSON(url) {
    const res = await fetch(url, { headers: { 'Accept': 'application/json' } });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status} ${res.statusText}: ${text}`);
    }
    return res.json();
  }

  async function fetchText(url) {
    const res = await fetch(url);
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status} ${res.statusText}: ${text}`);
    }
    return res.text();
  }

  async function postJSON(url, body) {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`HTTP ${res.status} ${res.statusText}: ${text}`);
    }
    return res.json();
  }

  // =============================================================================
  // Router
  // =============================================================================

  function parseRoute() {
    const raw = window.location.hash || ROUTES.overview;
    const hash = raw.startsWith('#') ? raw.slice(1) : raw;
    const parts = hash.split('/').filter(Boolean);

    if (parts.length === 0) return { name: 'overview', params: {} };

    const [first, second, ...rest] = parts;

    if (first === 'overview') return { name: 'overview', params: {} };
    if (first === 'events') return { name: 'events', params: {} };
    if (first === 'jobs') return { name: 'jobs', params: {} };
    if (first === 'runs') return { name: 'runs', params: {} };
    if (first === 'ports' && !second) return { name: 'ports', params: {} };
    if (first === 'ports' && second) {
      return { name: 'portDetail', params: { origin: decodeURIComponent(second + (rest.length ? '/' + rest.join('/') : '')) } };
    }
    if (first === 'bundles' && !second) return { name: 'bundles', params: {} };
    if (first === 'bundles' && second) return { name: 'bundle', params: { id: decodeURIComponent(second) } };

    return { name: 'overview', params: {} };
  }

  function setActiveNav(routeName) {
    const nav = $('#main-nav');
    if (!nav) return;
    $all('a.nav-link', nav).forEach((a) => {
      const r = a.dataset.route;
      a.classList.toggle('active', r === routeName);
    });
  }

  async function navigate() {
    const route = parseRoute();
    setActiveNav(route.name === 'portDetail' ? 'ports' : (route.name === 'bundle' ? 'bundles' : route.name));

    if (route.name === 'overview') {
      await renderOverview();
    } else if (route.name === 'events') {
      await renderEvents();
    } else if (route.name === 'jobs') {
      await renderJobs();
    } else if (route.name === 'runs') {
      await renderRuns();
    } else if (route.name === 'ports') {
      await renderPorts();
    } else if (route.name === 'portDetail') {
      await renderPortDetail(route.params.origin);
    } else if (route.name === 'bundles') {
      await renderBundles();
    } else if (route.name === 'bundle') {
      await renderBundle(route.params.id);
    } else {
      await renderOverview();
    }
  }

  // =============================================================================
  // SSE Client
  // =============================================================================

  function loadLastEventId() {
    const v = localStorage.getItem(STORAGE_KEYS.lastEventId);
    if (!v) return null;
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    return n;
  }

  function saveLastEventId(n) {
    if (n == null) return;
    localStorage.setItem(STORAGE_KEYS.lastEventId, String(n));
  }

  function setConnectionStatus(status, lastEventId = null) {
    state.connection.status = status;
    if (lastEventId != null) state.connection.lastEventId = lastEventId;

    const pill = $('#sse-status');
    const lastEl = $('#last-event-id');
    if (lastEl) lastEl.textContent = state.connection.lastEventId ?? '-';

    if (!pill) return;

    pill.classList.remove('text-bg-success', 'text-bg-danger', 'text-bg-secondary', 'text-bg-warning', 'sse-connecting');

    const text = $('.status-text', pill);
    if (status === 'connected') {
      pill.classList.add('text-bg-success');
      if (text) text.textContent = 'Connected';
    } else if (status === 'connecting') {
      pill.classList.add('text-bg-secondary', 'sse-connecting');
      if (text) text.textContent = 'Connecting…';
    } else if (status === 'error') {
      pill.classList.add('text-bg-danger');
      if (text) text.textContent = 'Error';
    } else {
      pill.classList.add('text-bg-secondary');
      if (text) text.textContent = 'Disconnected';
    }
  }

  function pushEvent(evt) {
    state.events.push(evt);
    if (state.events.length > MAX_EVENTS) {
      const overflow = state.events.length - MAX_EVENTS;
      state.events.splice(0, overflow);
    }
  }

  function handleIncomingEvent(eventType, payload, rawEventId) {
    const id = rawEventId != null ? Number(rawEventId) : payload?.id;
    const e = {
      id: Number.isFinite(id) ? id : null,
      type: eventType || payload?.type || 'event',
      ts: payload?.ts || payload?.timestamp || null,
      data: payload,
      receivedAt: new Date().toISOString(),
    };

    if (e.id != null) {
      state.connection.lastEventId = e.id;
      saveLastEventId(e.id);
    }

    pushEvent(e);

    // Lightweight toast heuristics
    if (e.type === 'job_failed') {
      toast('Job failed', `<div>${escapeHtml(payload?.job_id ?? '')}</div>`, {
        className: 'toast-job-failed',
        icon: 'exclamation-triangle',
        small: e.id != null ? `#${e.id}` : '',
      });
    } else if (e.type === 'pr_created') {
      toast('PR created', `<div>${escapeHtml(payload?.bundle_id ?? '')}</div>`, {
        className: 'toast-pr-created',
        icon: 'check-circle',
        small: e.id != null ? `#${e.id}` : '',
      });
    }

    // Update caches for informational events without triggering full re-render
    // (full re-render breaks stateful UI like tab selection)
    if (e.type === 'runner_status') {
      state.caches.runnerStatus = payload;
      // Only re-render if on overview page
      if (location.hash === '#/overview' || location.hash === '' || location.hash === '#/') {
        scheduleRender();
      }
      return;
    }
    if (e.type === 'activity') {
      // Prepend to cached activity log (keep last 20)
      if (!state.caches.activityLog) state.caches.activityLog = [];
      state.caches.activityLog.unshift(payload);
      if (state.caches.activityLog.length > 20) state.caches.activityLog.pop();
      // Only re-render if on overview page
      if (location.hash === '#/overview' || location.hash === '' || location.hash === '#/') {
        scheduleRender();
      }
      return;
    }

    // Trigger re-render for data-changing events (jobs, bundles, runs, etc.)
    scheduleRender();
  }

  function buildEventsUrl() {
    const last = state.connection.lastEventId ?? loadLastEventId();
    const params = new URLSearchParams();

    if (last != null) {
      params.set('after_id', String(last));
    } else {
      params.set('tail', '200');
    }

    return `/events?${params.toString()}`;
  }

  function stopSSE() {
    if (state.sse.connectTimer) {
      clearTimeout(state.sse.connectTimer);
      state.sse.connectTimer = null;
    }
    if (state.sse.es) {
      try { state.sse.es.close(); } catch (_) {}
      state.sse.es = null;
    }
    setConnectionStatus('disconnected');
  }

  function connectSSE() {
    stopSSE();

    const url = buildEventsUrl();
    setConnectionStatus('connecting');

    const es = new EventSource(url);
    state.sse.es = es;

    const onOpen = () => {
      state.sse.backoffMs = 500;
      setConnectionStatus('connected', state.connection.lastEventId ?? loadLastEventId());
    };

    const onError = () => {
      setConnectionStatus('error', state.connection.lastEventId ?? loadLastEventId());
      try { es.close(); } catch (_) {}

      const wait = state.sse.backoffMs;
      state.sse.backoffMs = Math.min(state.sse.backoffMs * 2, state.sse.backoffMaxMs);

      state.sse.connectTimer = setTimeout(() => {
        connectSSE();
      }, wait);
    };

    es.addEventListener('open', onOpen);
    es.addEventListener('error', onError);

    // Default message event
    es.onmessage = (msg) => {
      try {
        const payload = JSON.parse(msg.data);
        handleIncomingEvent(msg.type, payload, msg.lastEventId);
      } catch (e) {
        // ignore parse errors
      }
    };

    // Known event types
    Object.keys(EVENT_BADGES).forEach((t) => {
      es.addEventListener(t, (msg) => {
        try {
          const payload = JSON.parse(msg.data);
          handleIncomingEvent(t, payload, msg.lastEventId);
        } catch (_) {
          // ignore
        }
      });
    });
  }

  // =============================================================================
  // Rendering scheduler
  // =============================================================================

  let renderQueued = false;
  function scheduleRender() {
    if (renderQueued) return;
    renderQueued = true;
    window.requestAnimationFrame(async () => {
      renderQueued = false;
      await navigate();
    });
  }

  function setMain(html) {
    const el = $('#main-content');
    if (!el) return;
    el.innerHTML = html;
  }

  function setDetail(title, html) {
    const titleEl = $('#detail-title');
    const contentEl = $('#detail-content');
    const closeBtn = $('#close-detail');
    if (titleEl) titleEl.textContent = title;
    if (contentEl) contentEl.innerHTML = html;
    if (closeBtn) closeBtn.style.display = 'inline-block';

    // Offcanvas for mobile
    const offTitle = $('#offcanvas-title');
    const offContent = $('#offcanvas-content');
    if (offTitle) offTitle.textContent = title;
    if (offContent) offContent.innerHTML = html;

    const offEl = $('#detail-offcanvas');
    if (offEl && window.matchMedia('(max-width: 991px)').matches) {
      bootstrap.Offcanvas.getOrCreateInstance(offEl).show();
    }
  }

  function clearDetail() {
    state.selected = { kind: null, id: null };
    const titleEl = $('#detail-title');
    const contentEl = $('#detail-content');
    const closeBtn = $('#close-detail');
    if (titleEl) titleEl.textContent = 'Details';
    if (contentEl) contentEl.innerHTML = '<p class="text-body-secondary mb-0">Select an item to view details</p>';
    if (closeBtn) closeBtn.style.display = 'none';

    const offTitle = $('#offcanvas-title');
    const offContent = $('#offcanvas-content');
    if (offTitle) offTitle.textContent = 'Details';
    if (offContent) offContent.innerHTML = '<p class="text-body-secondary">Select an item to view details</p>';
  }

  // =============================================================================
  // Artifact viewer
  // =============================================================================

  function renderMarkdown(text) {
    const raw = marked.parse(text, { mangle: false, headerIds: false });
    const clean = DOMPurify.sanitize(raw);
    return `<div class="artifact-content markdown-body">${clean}</div>`;
  }

  function renderCode(text, lang = null) {
    const safe = escapeHtml(text);
    const languageClass = lang ? `language-${lang}` : '';
    return `
      <div class="code-block">
        <pre><code class="hljs ${languageClass}">${safe}</code></pre>
      </div>
    `;
  }

  function renderDiff(text) {
    const html = renderCode(text, 'diff');
    return `<div class="diff-view">${html}</div>`;
  }

  function highlightAll(root) {
    if (!window.hljs) return;
    $all('pre code', root).forEach((el) => {
      try { hljs.highlightElement(el); } catch (_) {}
    });
  }

  // =============================================================================
  // Detail renderer
  // =============================================================================

  function renderDetail(kind, data) {
    const pretty = escapeHtml(JSON.stringify(data, null, 2));
    return `
      <div class="d-flex justify-content-end mb-2">
        <button class="btn btn-sm btn-outline-secondary" data-action="copy-json">${icon('clipboard')} Copy JSON</button>
      </div>
      <div class="code-block">
        <pre><code class="hljs language-json">${pretty}</code></pre>
      </div>
    `;
  }

  function renderJobDetail(job, activities) {
    // Show error prominently if job failed
    let errorSection = '';
    if (job.state === 'failed') {
      const errorActivities = activities.filter(a => 
        a.stage?.includes('error') || a.stage?.includes('failed') || a.stage?.includes('timeout')
      );
      
      if (job.last_error) {
        errorSection = `
          <div class="alert alert-danger mb-3">
            <strong>Error:</strong>
            <pre class="mb-0 mt-2" style="white-space: pre-wrap;">${escapeHtml(job.last_error)}</pre>
          </div>
        `;
      } else if (errorActivities.length > 0) {
        const lastError = errorActivities[0];
        errorSection = `
          <div class="alert alert-danger mb-3">
            <strong>Error (from activity log):</strong>
            <div class="mt-2">${escapeHtml(lastError.message || 'Unknown error')}</div>
            <div class="small text-body-secondary mt-1">Stage: ${escapeHtml(lastError.stage || '-')}</div>
          </div>
        `;
      } else {
        errorSection = `
          <div class="alert alert-warning mb-3">
            <strong>Job failed</strong> - No error details available
          </div>
        `;
      }
    }

    // Activity log for this job
    let activitySection = '';
    if (activities.length > 0) {
      const rows = activities.slice(0, 10).map((a) => {
        const stageColor = getStageColor(a.stage);
        return `
          <tr>
            <td class="ts-col">${escapeHtml(formatTs(a.ts))}</td>
            <td>${badge(a.stage || '-', stageColor, 'badge-sm')}</td>
            <td>${escapeHtml(a.message || '')}</td>
          </tr>
        `;
      }).join('');
      
      activitySection = `
        <div class="card mb-3">
          <div class="card-header">Related Activity (${activities.length})</div>
          <div class="card-body p-0">
            <div class="table-responsive">
              <table class="table table-sm mb-0">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Stage</th>
                    <th>Message</th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>
          </div>
        </div>
      `;
    }

    // Job summary
    const summaryFields = [
      { label: 'Job ID', value: job.job_id },
      { label: 'State', value: job.state, badge: JOB_BADGES[job.state] },
      { label: 'Type', value: job.type },
      { label: 'Origin', value: job.origin },
      { label: 'Created', value: formatTs(job.created_ts_utc) },
      { label: 'Bundle', value: job.bundle_dir },
    ].filter(f => f.value);

    const summaryHtml = summaryFields.map(f => `
      <tr>
        <th class="text-body-secondary" style="width: 100px;">${escapeHtml(f.label)}</th>
        <td>${f.badge ? badge(f.value, f.badge) : escapeHtml(f.value)}</td>
      </tr>
    `).join('');

    const pretty = escapeHtml(JSON.stringify(job, null, 2));
    
    return `
      ${errorSection}
      <div class="card mb-3">
        <div class="card-header">Job Summary</div>
        <div class="card-body p-0">
          <table class="table table-sm mb-0">
            <tbody>${summaryHtml}</tbody>
          </table>
        </div>
      </div>
      ${activitySection}
      <div class="d-flex justify-content-end mb-2">
        <button class="btn btn-sm btn-outline-secondary" data-action="copy-json">${icon('clipboard')} Copy JSON</button>
      </div>
      <div class="code-block">
        <pre><code class="hljs language-json">${pretty}</code></pre>
      </div>
    `;
  }

  async function copyToClipboard(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast('Copied', '<div>Copied to clipboard</div>', { icon: 'clipboard-check', delay: 2000 });
    } catch (_) {
      // fallback
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
      toast('Copied', '<div>Copied to clipboard</div>', { icon: 'clipboard-check', delay: 2000 });
    }
  }

  // =============================================================================
  // Views
  // =============================================================================

  function renderEmpty(title, subtitle) {
    return `
      <div class="empty-state">
        <i class="bi bi-inbox"></i>
        <h5 class="mt-3">${escapeHtml(title)}</h5>
        <div>${escapeHtml(subtitle || '')}</div>
      </div>
    `;
  }

  async function renderOverview() {
    let status;
    try {
      status = await fetchJSON('/status');
    } catch (e) {
      setMain(`
        <div class="alert alert-danger">Failed to load /status: ${escapeHtml(String(e))}</div>
      `);
      return;
    }

    // Fetch activity log and runner status in parallel
    let activityLog = [];
    let runnerStatus = null;
    try {
      const [activityData, runnerData] = await Promise.all([
        fetchJSON('/activity').catch(() => ({ activities: [] })),
        fetchJSON('/runner-status').catch(() => null),
      ]);
      activityLog = activityData.activities || [];
      runnerStatus = runnerData;
      state.caches.activityLog = activityLog;
      state.caches.runnerStatus = runnerStatus;
    } catch (_) {
      // Use cached values if fetch fails
      activityLog = state.caches.activityLog || [];
      runnerStatus = state.caches.runnerStatus;
    }

    const jobs = status.jobs || {};

    const pending = jobs.pending || 0;
    const inflight = jobs.inflight || 0;
    const done = jobs.done || 0;
    const failed = jobs.failed || 0;
    const total = pending + inflight + done + failed;

    const pct = (n) => (total ? Math.round((n / total) * 100) : 0);

    const latestEvents = state.events.slice(-20).reverse();

    const latestHtml = latestEvents.length
      ? `
        <div class="table-responsive">
          <table class="table table-sm table-events mb-0">
            <thead>
              <tr>
                <th>Ts</th>
                <th>Type</th>
                <th>Origin</th>
                <th>ID</th>
              </tr>
            </thead>
            <tbody>
              ${latestEvents.map(renderEventRowTbody).join('')}
            </tbody>
          </table>
        </div>
      `
      : renderEmpty('No events yet', 'Waiting for SSE stream…');

    // Render runner status
    const runnerStatusHtml = renderRunnerStatus(runnerStatus);

    // Render activity log
    const activityHtml = renderActivityLog(activityLog);

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <h4 class="mb-0">Overview</h4>
        <div class="text-body-secondary small">Last event: ${escapeHtml(String(state.connection.lastEventId ?? status.last_event_id ?? '-'))}</div>
      </div>

      <div class="row g-3">
        <div class="col-md-6">
          <div class="card shadow-sm">
            <div class="card-body">
              <h6 class="card-title">Counts</h6>
              <div class="d-flex flex-wrap gap-2">
                ${badge(`Runs: ${status.runs ?? 0}`, 'secondary')}
                ${badge(`Bundles: ${status.bundles ?? 0}`, 'secondary')}
              </div>
              <hr />
              <h6 class="card-title">Job Queue</h6>
              <div class="d-flex flex-wrap gap-2 mb-2">
                ${badge(`Pending: ${pending}`, JOB_BADGES.pending)}
                ${badge(`Inflight: ${inflight}`, JOB_BADGES.inflight)}
                ${badge(`Done: ${done}`, JOB_BADGES.done)}
                ${badge(`Failed: ${failed}`, JOB_BADGES.failed)}
              </div>
              <div class="progress queue-progress" role="progressbar" aria-label="Queue progress">
                <div class="progress-bar bg-secondary" style="width:${pct(pending)}%"></div>
                <div class="progress-bar bg-primary" style="width:${pct(inflight)}%"></div>
                <div class="progress-bar bg-success" style="width:${pct(done)}%"></div>
                <div class="progress-bar bg-danger" style="width:${pct(failed)}%"></div>
              </div>
              <div class="text-body-secondary small mt-2">Total jobs: ${total}</div>
            </div>
          </div>
        </div>

        <div class="col-md-6">
          <div class="card shadow-sm">
            <div class="card-body">
              <h6 class="card-title">Runner Status</h6>
              ${runnerStatusHtml}
              <hr />
              <h6 class="card-title">Stream</h6>
              <div class="d-flex flex-wrap gap-2">
                ${badge(`SSE: ${state.connection.status}`, state.connection.status === 'connected' ? 'success' : (state.connection.status === 'error' ? 'danger' : 'secondary'))}
                ${badge(`Paused: ${state.paused ? 'yes' : 'no'}`, state.paused ? 'warning' : 'secondary')}
                ${badge(`Buffer: ${state.events.length}`, 'secondary')}
              </div>
            </div>
          </div>
        </div>

        <div class="col-12">
          <div class="card shadow-sm">
            <div class="card-header d-flex align-items-center justify-content-between">
              <span>Activity Log</span>
              <button class="btn btn-sm btn-outline-secondary" data-action="refresh-activity">${icon('arrow-clockwise')} Refresh</button>
            </div>
            <div class="card-body p-0">
              ${activityHtml}
            </div>
          </div>
        </div>

        <div class="col-12">
          <div class="card shadow-sm">
            <div class="card-header d-flex align-items-center justify-content-between">
              <span>Latest events</span>
              <a class="btn btn-sm btn-outline-secondary" href="#/events">Open Events</a>
            </div>
            <div class="card-body p-0">
              ${latestHtml}
            </div>
          </div>
        </div>
      </div>
    `);

    attachOverviewHandlers();
  }

  function renderRunnerStatus(runnerStatus) {
    if (!runnerStatus) {
      return `
        <div class="d-flex flex-wrap gap-2 align-items-center">
          ${badge('unknown', RUNNER_STATUS_BADGES.unknown)}
          <span class="text-body-secondary small">Runner status not available</span>
        </div>
      `;
    }

    const statusText = runnerStatus.is_stale ? 'stale' : runnerStatus.status;
    const statusColor = runnerStatus.is_stale ? RUNNER_STATUS_BADGES.stale : (RUNNER_STATUS_BADGES[runnerStatus.status] || 'secondary');

    let details = '';
    if (runnerStatus.status === 'processing' && runnerStatus.job_id) {
      details = `<div class="small text-body-secondary mt-1">Job: ${escapeHtml(runnerStatus.job_id)}</div>`;
      if (runnerStatus.current_stage) {
        details += `<div class="small text-body-secondary">Stage: ${escapeHtml(runnerStatus.current_stage)}</div>`;
      }
    }

    const updatedAgo = runnerStatus.updated_at ? formatTimeAgo(runnerStatus.updated_at) : 'unknown';

    return `
      <div class="d-flex flex-wrap gap-2 align-items-center">
        ${badge(statusText, statusColor)}
        <span class="text-body-secondary small">Updated ${updatedAgo}</span>
      </div>
      ${details}
    `;
  }

  function renderActivityLog(activities) {
    if (!activities.length) {
      return renderEmpty('No activity', 'Runner has not logged any activity yet.');
    }

    const rows = activities.map((a) => {
      const stageColor = getStageColor(a.stage);
      const durationText = a.duration_ms ? `${a.duration_ms}ms` : '';
      return `
        <tr>
          <td class="ts-col">${escapeHtml(formatTs(a.ts))}</td>
          <td>${badge(a.stage, stageColor, 'badge-sm')}</td>
          <td>${escapeHtml(a.message)}</td>
          <td class="text-end text-body-secondary small">${escapeHtml(durationText)}</td>
        </tr>
      `;
    }).join('');

    return `
      <div class="table-responsive">
        <table class="table table-sm mb-0">
          <thead>
            <tr>
              <th>Time</th>
              <th>Stage</th>
              <th>Message</th>
              <th class="text-end">Duration</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  }

  function getStageColor(stage) {
    if (!stage) return 'secondary';
    if (stage.includes('error') || stage.includes('failed') || stage.includes('timeout')) return 'danger';
    if (stage.includes('success') || stage.includes('complete') || stage.includes('done')) return 'success';
    if (stage.includes('start') || stage.includes('running')) return 'primary';
    if (stage.includes('api_call')) return 'info';
    if (stage.includes('enqueue')) return 'warning';
    return 'secondary';
  }

  function formatTimeAgo(isoTs) {
    if (!isoTs) return 'unknown';
    const d = new Date(isoTs);
    if (Number.isNaN(d.getTime())) return isoTs;
    const now = Date.now();
    const diff = now - d.getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 5) return 'just now';
    if (seconds < 60) return `${seconds}s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return d.toLocaleString();
  }

  function attachOverviewHandlers() {
    // events in table clickable via delegation
    const root = $('#main-content');
    if (!root) return;
    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'refresh-activity') {
        try {
          const [activityData, runnerData] = await Promise.all([
            fetchJSON('/activity'),
            fetchJSON('/runner-status').catch(() => null),
          ]);
          state.caches.activityLog = activityData.activities || [];
          state.caches.runnerStatus = runnerData;
          scheduleRender();
        } catch (err) {
          toast('Error', `<div>Failed to refresh activity</div>`, { icon: 'exclamation-triangle' });
        }
        return;
      }

      const row = e.target.closest('tr[data-event-id]');
      if (!row) return;
      const id = Number(row.dataset.eventId);
      const evt = state.events.find((x) => x.id === id);
      if (!evt) return;
      state.selected = { kind: 'event', id };
      setDetail(`Event #${id}`, renderDetail('event', evt));
      highlightAll($('#detail-content'));
      bindDetailActions(evt);
    });
  }

  function renderEventRowTbody(evt) {
    const bs = EVENT_BADGES[evt.type] || 'secondary';
    const origin = evt.data?.origin || evt.data?.bundle_id || evt.data?.job_id || '';

    return `
      <tr class="event-row" data-event-id="${evt.id ?? ''}">
        <td class="ts-col">${escapeHtml(formatTs(evt.ts))}</td>
        <td>${badge(evt.type, bs, 'badge-sm')}</td>
        <td class="origin-col">${escapeHtml(origin)}</td>
        <td class="id-col">${evt.id != null ? `#${escapeHtml(String(evt.id))}` : '-'}</td>
      </tr>
    `;
  }

  function filteredEvents() {
    const { eventType, origin, onlyFailures } = state.filters;
    const normOrigin = (origin || '').trim().toLowerCase();

    return state.events.filter((e) => {
      if (eventType && e.type !== eventType) return false;
      if (onlyFailures && e.type !== 'job_failed') return false;
      if (normOrigin) {
        const o = String(e.data?.origin ?? e.data?.bundle_id ?? e.data?.job_id ?? '').toLowerCase();
        if (!o.includes(normOrigin)) return false;
      }
      return true;
    });
  }

  async function renderEvents() {
    const types = Array.from(new Set(state.events.map((e) => e.type))).sort();
    const selectedType = state.filters.eventType || '';

    const list = filteredEvents().slice().reverse();

    setMain(`
      <div class="filter-bar border-bottom pb-2 mb-3">
        <div class="d-flex align-items-center justify-content-between">
          <h4 class="mb-0">Events</h4>
          <div class="d-flex align-items-center gap-2">
            <button class="btn btn-sm btn-outline-secondary" data-action="clear-events">${icon('trash')} Clear buffer</button>
          </div>
        </div>

        <div class="row g-2 mt-1 align-items-end">
          <div class="col-md-4">
            <label class="form-label small text-body-secondary">Type</label>
            <select class="form-select form-select-sm" id="event-type">
              <option value="">All</option>
              ${types.map((t) => `<option value="${escapeHtml(t)}" ${t === selectedType ? 'selected' : ''}>${escapeHtml(t)}</option>`).join('')}
            </select>
          </div>
          <div class="col-md-5">
            <label class="form-label small text-body-secondary">Origin / ID filter</label>
            <input class="form-control form-control-sm" id="event-origin" placeholder="e.g. lang/python" value="${escapeHtml(state.filters.origin)}" />
          </div>
          <div class="col-md-3">
            <div class="form-check mt-4">
              <input class="form-check-input" type="checkbox" id="only-failures" ${state.filters.onlyFailures ? 'checked' : ''}>
              <label class="form-check-label" for="only-failures">Only failures</label>
            </div>
          </div>
        </div>
      </div>

      <div class="card shadow-sm">
        <div class="card-body p-0">
          ${list.length ? `
            <div class="table-responsive">
              <table class="table table-hover table-events mb-0">
                <thead>
                  <tr>
                    <th>Ts</th>
                    <th>Type</th>
                    <th>Origin</th>
                    <th>ID</th>
                  </tr>
                </thead>
                <tbody>
                  ${list.map(renderEventRowTbody).join('')}
                </tbody>
              </table>
            </div>
            <div class="text-body-secondary small p-2 border-top">Showing ${list.length} of ${state.events.length} buffered events</div>
          ` : renderEmpty('No events', 'Try waiting for SSE or clear filters.')}
        </div>
      </div>
    `);

    const root = $('#main-content');
    if (!root) return;

    $('#event-type', root)?.addEventListener('change', (e) => {
      state.filters.eventType = e.target.value || null;
      scheduleRender();
    });
    $('#event-origin', root)?.addEventListener('input', (e) => {
      state.filters.origin = e.target.value;
      scheduleRender();
    });
    $('#only-failures', root)?.addEventListener('change', (e) => {
      state.filters.onlyFailures = e.target.checked;
      scheduleRender();
    });

    root.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'clear-events') {
        state.events = [];
        clearDetail();
        scheduleRender();
        return;
      }

      const row = e.target.closest('tr[data-event-id]');
      if (!row) return;
      const id = Number(row.dataset.eventId);
      const evt = state.events.find((x) => x.id === id);
      if (!evt) return;
      state.selected = { kind: 'event', id };
      setDetail(`Event #${id}`, renderDetail('event', evt));
      highlightAll($('#detail-content'));
      bindDetailActions(evt);

      $all('tr.event-row', root).forEach((tr) => tr.classList.toggle('selected', tr === row));
    });
  }

  function bindDetailActions(contextObj) {
    const root = $('#detail-content');
    if (!root) return;

    root.addEventListener('click', (e) => {
      const a = e.target.closest('[data-action]');
      if (!a) return;

      if (a.dataset.action === 'copy-json') {
        copyToClipboard(JSON.stringify(contextObj, null, 2));
      }
    }, { once: true });
  }

  async function renderJobs() {
    let jobs;
    try {
      const data = await fetchJSON('/jobs');
      jobs = data.jobs || [];
    } catch (e) {
      setMain(`<div class="alert alert-danger">Failed to load /jobs: ${escapeHtml(String(e))}</div>`);
      return;
    }

    jobs.forEach((j) => { state.jobsById[j.job_id] = j; });

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <h4 class="mb-0">Jobs</h4>
        <button class="btn btn-sm btn-outline-secondary" data-action="refresh-jobs">${icon('arrow-clockwise')} Refresh</button>
      </div>

      <div class="card shadow-sm">
        <div class="card-body p-0">
          ${jobs.length ? `
            <div class="table-responsive">
              <table class="table table-hover mb-0">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>State</th>
                    <th>Type</th>
                    <th>Origin</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  ${jobs.map((j) => {
                    const bs = JOB_BADGES[j.state] || 'secondary';
                    return `
                      <tr class="event-row" data-job-id="${escapeHtml(j.job_id)}">
                        <td class="id-col">${escapeHtml(j.job_id)}</td>
                        <td>${badge(j.state, bs, 'badge-sm')}</td>
                        <td>${escapeHtml(j.type || '')}</td>
                        <td class="origin-col">${j.origin ? `<a href="#/ports/${encodeURIComponent(j.origin)}">${escapeHtml(j.origin)}</a>` : '-'}</td>
                        <td class="ts-col">${escapeHtml(formatTs(j.created_ts_utc))}</td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          ` : renderEmpty('No jobs', 'Did you point state-server at a logs root?')}
        </div>
      </div>
    `);

    const root = $('#main-content');
    if (!root) return;

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'refresh-jobs') {
        scheduleRender();
        return;
      }

      const row = e.target.closest('tr[data-job-id]');
      if (!row) return;
      const id = row.dataset.jobId;

      try {
        // Fetch job and activity log in parallel
        const [jobData, activityData] = await Promise.all([
          fetchJSON(`/jobs/${encodeURIComponent(id)}`),
          fetchJSON('/activity').catch(() => ({ activities: [] })),
        ]);
        const job = jobData.job;
        state.jobsById[id] = job;
        state.selected = { kind: 'job', id };
        
        // Filter activities related to this job
        const jobPrefix = id.split('.')[0]; // Get timestamp-profile-origin-pid prefix
        const relatedActivities = (activityData.activities || []).filter(a => 
          a.job_id === id || a.job_id?.startsWith(jobPrefix)
        );
        
        setDetail(`Job ${shortId(id, 24)}`, renderJobDetail(job, relatedActivities));
        highlightAll($('#detail-content'));
        bindDetailActions(job);
      } catch (err) {
        toast('Error', `<div>Failed to load job</div><div class="small text-body-secondary">${escapeHtml(String(err))}</div>`, { icon: 'exclamation-triangle' });
      }
    });
  }

  async function renderRuns() {
    let runs;
    try {
      const data = await fetchJSON('/runs');
      runs = data.runs || [];
    } catch (e) {
      setMain(`<div class="alert alert-danger">Failed to load /runs: ${escapeHtml(String(e))}</div>`);
      return;
    }

    runs.forEach((r) => { state.runsById[r.run_id] = r; });

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <h4 class="mb-0">Runs</h4>
        <button class="btn btn-sm btn-outline-secondary" data-action="refresh-runs">${icon('arrow-clockwise')} Refresh</button>
      </div>

      <div class="card shadow-sm">
        <div class="card-body p-0">
          ${runs.length ? `
            <div class="table-responsive">
              <table class="table table-hover mb-0">
                <thead>
                  <tr>
                    <th>Run</th>
                    <th>Profile</th>
                    <th>Start</th>
                    <th>End</th>
                  </tr>
                </thead>
                <tbody>
                  ${runs.map((r) => `
                    <tr class="event-row" data-run-id="${escapeHtml(r.run_id)}">
                      <td class="id-col">${escapeHtml(r.run_id)}</td>
                      <td>${escapeHtml(r.profile || '')}</td>
                      <td class="ts-col">${escapeHtml(formatTs(r.ts_start))}</td>
                      <td class="ts-col">${escapeHtml(formatTs(r.ts_end))}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          ` : renderEmpty('No runs', 'No runs found under evidence/runs.')}
        </div>
      </div>

      <div class="text-body-secondary small mt-2">Run detail page not implemented yet; select a run for JSON.</div>
    `);

    const root = $('#main-content');
    if (!root) return;

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'refresh-runs') {
        scheduleRender();
        return;
      }

      const row = e.target.closest('tr[data-run-id]');
      if (!row) return;
      const id = row.dataset.runId;
      try {
        const data = await fetchJSON(`/runs/${encodeURIComponent(id)}`);
        state.selected = { kind: 'run', id };
        setDetail(`Run ${shortId(id, 28)}`, renderDetail('run', data));
        highlightAll($('#detail-content'));
        bindDetailActions(data);
      } catch (err) {
        toast('Error', `<div>Failed to load run</div><div class="small text-body-secondary">${escapeHtml(String(err))}</div>`, { icon: 'exclamation-triangle' });
      }
    });
  }

  async function renderBundles() {
    let bundles;
    try {
      const data = await fetchJSON('/bundles');
      bundles = data.bundles || [];
    } catch (e) {
      setMain(`<div class="alert alert-danger">Failed to load /bundles: ${escapeHtml(String(e))}</div>`);
      return;
    }

    bundles.forEach((b) => { state.bundlesById[b.bundle_id] = b; });

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <h4 class="mb-0">Bundles</h4>
        <button class="btn btn-sm btn-outline-secondary" data-action="refresh-bundles">${icon('arrow-clockwise')} Refresh</button>
      </div>

      <div class="card shadow-sm">
        <div class="card-body p-0">
          ${bundles.length ? `
            <div class="table-responsive">
              <table class="table table-hover mb-0">
                <thead>
                  <tr>
                    <th>Bundle</th>
                    <th>Origin</th>
                    <th>Result</th>
                    <th>Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  ${bundles.map((b) => `
                    <tr class="event-row" data-bundle-id="${escapeHtml(b.bundle_id)}">
                      <td class="id-col"><a href="#/bundles/${encodeURIComponent(b.bundle_id)}">${escapeHtml(shortId(b.bundle_id, 32))}</a></td>
                      <td class="origin-col">${b.origin ? `<a href="#/ports/${encodeURIComponent(b.origin)}">${escapeHtml(b.origin)}</a>` : '-'}</td>
                      <td>${escapeHtml(b.result || '')}</td>
                      <td class="ts-col">${escapeHtml(formatTs(b.ts_utc))}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
            <div class="text-body-secondary small p-2 border-top">Showing ${bundles.length} most recent bundles</div>
          ` : renderEmpty('No bundles', 'No evidence bundles found.')}
        </div>
      </div>
    `);

    const root = $('#main-content');
    if (!root) return;

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'refresh-bundles') {
        scheduleRender();
        return;
      }

      const row = e.target.closest('tr[data-bundle-id]');
      if (!row) return;
      const id = row.dataset.bundleId;

      // Navigate to bundle detail
      window.location.hash = `#/bundles/${encodeURIComponent(id)}`;
    });
  }

  async function buildPortsIndexFromEvents() {
    // Gather origin strings from known event payloads / bundle_created / job_*.
    const origins = new Set();

    for (const e of state.events) {
      const o = e.data?.origin;
      if (o && String(o).includes('/')) origins.add(String(o));
    }

    // also pull from jobs list (best effort)
    try {
      const data = await fetchJSON('/jobs');
      for (const j of data.jobs || []) {
        if (j.origin && String(j.origin).includes('/')) origins.add(String(j.origin));
      }
    } catch (_) {
      // ignore
    }

    state.caches.portsIndex = {
      origins: Array.from(origins).sort(),
      lastBuiltAt: Date.now(),
    };
  }

  async function renderPorts() {
    if (!state.caches.portsIndex) {
      await buildPortsIndexFromEvents();
    }

    const q = (state.filters.origin || '').trim().toLowerCase();
    const origins = state.caches.portsIndex?.origins || [];
    const filtered = q ? origins.filter((o) => o.toLowerCase().includes(q)) : origins;

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <h4 class="mb-0">Ports</h4>
        <button class="btn btn-sm btn-outline-secondary" data-action="rebuild-ports">${icon('arrow-repeat')} Rebuild index</button>
      </div>

      <div class="card shadow-sm mb-3">
        <div class="card-body">
          <label class="form-label small text-body-secondary">Search origins</label>
          <input class="form-control" id="ports-search" placeholder="e.g. lang/python" value="${escapeHtml(state.filters.origin)}" />
          <div class="text-body-secondary small mt-2">Index built from events buffer + /jobs. For full listing, add a server endpoint later.</div>
        </div>
      </div>

      <div class="card shadow-sm">
        <div class="card-body p-0">
          ${filtered.length ? `
            <ul class="list-group list-group-flush">
              ${filtered.slice(0, 500).map((o) => `
                <a class="list-group-item list-group-item-action" href="#/ports/${encodeURIComponent(o)}">
                  ${icon('box-seam')} <span class="ms-2">${escapeHtml(o)}</span>
                </a>
              `).join('')}
            </ul>
            <div class="text-body-secondary small p-2 border-top">Showing ${Math.min(filtered.length, 500)} of ${filtered.length}</div>
          ` : renderEmpty('No ports', 'No origins discovered yet from events/jobs.')}
        </div>
      </div>
    `);

    const root = $('#main-content');
    if (!root) return;

    $('#ports-search', root)?.addEventListener('input', (e) => {
      state.filters.origin = e.target.value;
      scheduleRender();
    });

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'rebuild-ports') {
        state.caches.portsIndex = null;
        await buildPortsIndexFromEvents();
        scheduleRender();
      }
    });
  }

  async function renderPortDetail(origin) {
    let data;
    try {
      data = await fetchJSON(`/ports/${encodeURIComponent(origin)}`);
    } catch (e) {
      setMain(`<div class="alert alert-danger">Failed to load /ports/${escapeHtml(origin)}: ${escapeHtml(String(e))}</div>`);
      return;
    }

    const bundles = data.bundles || [];
    const jobs = data.jobs || [];

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <div>
          <h4 class="mb-0">Port</h4>
          <div class="text-body-secondary">${mono(origin)}</div>
        </div>
        <div class="d-flex gap-2">
          <a class="btn btn-sm btn-outline-secondary" href="#/ports">${icon('arrow-left')} Back</a>
          <button class="btn btn-sm btn-outline-secondary" data-action="refresh-port">${icon('arrow-clockwise')} Refresh</button>
        </div>
      </div>

      <div class="row g-3">
        <div class="col-12">
          <div class="card shadow-sm">
            <div class="card-header">Bundles</div>
            <div class="card-body p-0">
              ${bundles.length ? `
                <div class="table-responsive">
                  <table class="table table-hover mb-0">
                    <thead>
                      <tr>
                        <th>Bundle</th>
                        <th>Run</th>
                        <th>Ts</th>
                        <th>Result</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${bundles.map((b) => `
                        <tr class="event-row" data-bundle-id="${escapeHtml(b.bundle_id)}">
                          <td class="id-col"><a href="#/bundles/${encodeURIComponent(b.bundle_id)}">${escapeHtml(b.bundle_id)}</a></td>
                          <td class="id-col">${escapeHtml(b.run_id || '')}</td>
                          <td class="ts-col">${escapeHtml(formatTs(b.ts_utc))}</td>
                          <td>${escapeHtml(b.result || '')}</td>
                        </tr>
                      `).join('')}
                    </tbody>
                  </table>
                </div>
              ` : renderEmpty('No bundles', 'No bundle records for this origin.')}
            </div>
          </div>
        </div>

        <div class="col-12">
          <div class="card shadow-sm">
            <div class="card-header">Jobs</div>
            <div class="card-body p-0">
              ${jobs.length ? `
                <div class="table-responsive">
                  <table class="table table-hover mb-0">
                    <thead>
                      <tr>
                        <th>Job</th>
                        <th>State</th>
                        <th>Type</th>
                        <th>Created</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${jobs.map((j) => {
                        const bs = JOB_BADGES[j.state] || 'secondary';
                        return `
                          <tr class="event-row" data-job-id="${escapeHtml(j.job_id)}">
                            <td class="id-col">${escapeHtml(j.job_id)}</td>
                            <td>${badge(j.state, bs, 'badge-sm')}</td>
                            <td>${escapeHtml(j.type || '')}</td>
                            <td class="ts-col">${escapeHtml(formatTs(j.created_ts_utc))}</td>
                          </tr>
                        `;
                      }).join('')}
                    </tbody>
                  </table>
                </div>
              ` : renderEmpty('No jobs', 'No job records for this origin.')}
            </div>
          </div>
        </div>
      </div>
    `);

    const root = $('#main-content');
    if (!root) return;

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (btn?.dataset.action === 'refresh-port') {
        scheduleRender();
        return;
      }

      const jobRow = e.target.closest('tr[data-job-id]');
      if (jobRow) {
        const id = jobRow.dataset.jobId;
        try {
          const j = await fetchJSON(`/jobs/${encodeURIComponent(id)}`);
          setDetail(`Job ${shortId(id, 24)}`, renderDetail('job', j.job));
          highlightAll($('#detail-content'));
          bindDetailActions(j.job);
        } catch (_) {}
      }

      const bundleRow = e.target.closest('tr[data-bundle-id]');
      if (bundleRow) {
        const id = bundleRow.dataset.bundleId;
        window.location.hash = `#/bundles/${encodeURIComponent(id)}`;
      }
    });
  }

  function pickDefaultBundleTab(artifacts) {
    const hasTriage = artifacts.some((a) => a.relpath === 'analysis/triage.md');
    const hasPatch = artifacts.some((a) => a.relpath === 'analysis/patch.diff');

    if (hasTriage) return 'triage';
    if (hasPatch) return 'patch';
    return 'summary';
  }

  async function renderBundle(bundleId) {
    let data;
    try {
      data = await fetchJSON(`/bundles/${encodeURIComponent(bundleId)}`);
    } catch (e) {
      setMain(`<div class="alert alert-danger">Failed to load /bundles/${escapeHtml(bundleId)}: ${escapeHtml(String(e))}</div>`);
      return;
    }

    const bundle = data.bundle;
    const artifacts = data.artifacts || [];
    state.bundlesById[bundleId] = bundle;

    const defaultTab = pickDefaultBundleTab(artifacts);

    const pinned = [
      { label: 'meta.txt', relpath: 'meta.txt' },
      { label: 'errors.txt', relpath: 'logs/errors.txt' },
      { label: 'triage.md', relpath: 'analysis/triage.md' },
      { label: 'patch.diff', relpath: 'analysis/patch.diff' },
      { label: 'patch.md', relpath: 'analysis/patch.md' },
      { label: 'pr_url.txt', relpath: 'analysis/pr_url.txt' },
    ].filter((p) => artifacts.some((a) => a.relpath === p.relpath));

    let contextData = null;
    let requestData = null;
    if (bundle?.run_id && bundle?.origin) {
      try {
        contextData = await fetchJSON(`/user-context?run_id=${encodeURIComponent(bundle.run_id)}&origin=${encodeURIComponent(bundle.origin)}`);
      } catch (_) {
        contextData = null;
      }
      try {
        requestData = await fetchJSON(`/user-context-request?bundle_id=${encodeURIComponent(bundleId)}`);
      } catch (_) {
        requestData = null;
      }
    }

    const request = requestData?.request ?? null;
    const context = contextData?.context ?? null;
    const needsContext = request?.status === 'pending';
    const contextPanel = needsContext ? `
      <div class="alert alert-warning">
        <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
          <div>
            <strong>Additional context needed</strong>
            <div class="small text-body-secondary mt-1">
              Confidence is ${escapeHtml(request?.confidence || 'unknown')} (${escapeHtml(request?.classification || 'unknown')}).
              Add any extra clues or direction to help triage.
            </div>
          </div>
          <button class="btn btn-sm btn-outline-secondary" data-action="open-errors">${icon('file-earmark-text')} Open errors.txt</button>
        </div>
        <div class="mt-3">
          <textarea class="form-control" rows="4" data-context-input placeholder="Any extra clues, expected fix direction, or environment notes...">${escapeHtml(context?.context_text || '')}</textarea>
        </div>
        <div class="mt-2 d-flex gap-2">
          <button class="btn btn-sm btn-primary" data-action="submit-user-context" data-run-id="${escapeHtml(bundle.run_id)}" data-origin="${escapeHtml(bundle.origin)}">Save &amp; Re-run triage</button>
          <div class="small text-body-secondary align-self-center">Scoped to ${escapeHtml(bundle.run_id)} / ${escapeHtml(bundle.origin)}</div>
        </div>
      </div>
    ` : '';

    setMain(`
      <div class="d-flex align-items-center justify-content-between mb-3">
        <div>
          <h4 class="mb-0">Bundle</h4>
          <div class="text-body-secondary">${mono(bundleId)}</div>
        </div>
        <div class="d-flex gap-2">
          ${bundle?.origin ? `<a class="btn btn-sm btn-outline-secondary" href="#/ports/${encodeURIComponent(bundle.origin)}">${icon('box-seam')} Port</a>` : ''}
          <button class="btn btn-sm btn-outline-secondary" data-action="refresh-bundle">${icon('arrow-clockwise')} Refresh</button>
        </div>
      </div>

      ${pinned.length ? `
        <div class="pinned-artifacts">
          ${pinned.map((p) => `<button class="btn btn-sm btn-outline-secondary" data-action="open-artifact" data-relpath="${escapeHtml(p.relpath)}">${escapeHtml(p.label)}</button>`).join('')}
        </div>
      ` : ''}

      ${contextPanel}

      <div class="card shadow-sm mb-3">
        <div class="card-body">
          <div class="row g-2">
            <div class="col-md-6">
              <div class="text-body-secondary small">Origin</div>
              <div>${bundle?.origin ? `<a href="#/ports/${encodeURIComponent(bundle.origin)}">${escapeHtml(bundle.origin)}</a>` : '-'}</div>
            </div>
            <div class="col-md-3">
              <div class="text-body-secondary small">Run</div>
              <div class="id-col">${escapeHtml(bundle?.run_id || '')}</div>
            </div>
            <div class="col-md-3">
              <div class="text-body-secondary small">Result</div>
              <div>${escapeHtml(bundle?.result || '')}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="card shadow-sm bundle-tabs">
        <div class="card-header p-0">
          <ul class="nav nav-tabs" role="tablist">
            <li class="nav-item" role="presentation">
              <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-summary" type="button" role="tab">Summary</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-triage" type="button" role="tab" ${artifacts.some((a) => a.relpath === 'analysis/triage.md') ? '' : 'disabled'}>Triage</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-patch" type="button" role="tab" ${artifacts.some((a) => a.relpath === 'analysis/patch.diff') ? '' : 'disabled'}>Patch</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-logs" type="button" role="tab" ${artifacts.some((a) => a.relpath === 'logs/errors.txt') ? '' : 'disabled'}>Logs</button>
            </li>
            <li class="nav-item" role="presentation">
              <button class="nav-link" data-bs-toggle="tab" data-bs-target="#tab-artifacts" type="button" role="tab">Artifacts (${artifacts.length})</button>
            </li>
          </ul>
        </div>
        <div class="tab-content">
          <div class="tab-pane fade" id="tab-summary" role="tabpanel">
            ${renderBundleSummary(bundle, artifacts)}
          </div>
          <div class="tab-pane fade" id="tab-triage" role="tabpanel">
            <div class="text-body-secondary">Select triage.md (pinned) to load.</div>
          </div>
          <div class="tab-pane fade" id="tab-patch" role="tabpanel">
            <div class="text-body-secondary">Select patch.diff (pinned) to load.</div>
          </div>
          <div class="tab-pane fade" id="tab-logs" role="tabpanel">
            <div class="text-body-secondary">Select errors.txt (pinned) to load.</div>
          </div>
          <div class="tab-pane fade" id="tab-artifacts" role="tabpanel">
            ${renderArtifactsList(bundleId, artifacts)}
          </div>
        </div>
      </div>
    `);

    const root = $('#main-content');
    if (!root) return;

    // activate default tab
    const defaultBtn = {
      summary: root.querySelector('[data-bs-target="#tab-summary"]'),
      triage: root.querySelector('[data-bs-target="#tab-triage"]'),
      patch: root.querySelector('[data-bs-target="#tab-patch"]'),
      logs: root.querySelector('[data-bs-target="#tab-logs"]'),
      artifacts: root.querySelector('[data-bs-target="#tab-artifacts"]'),
    }[defaultTab];

    if (defaultBtn && !defaultBtn.disabled) {
      bootstrap.Tab.getOrCreateInstance(defaultBtn).show();
    } else {
      bootstrap.Tab.getOrCreateInstance(root.querySelector('[data-bs-target="#tab-summary"]')).show();
    }

    root.addEventListener('click', async (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn) return;

      if (btn.dataset.action === 'refresh-bundle') {
        scheduleRender();
        return;
      }

      if (btn.dataset.action === 'open-errors') {
        await openArtifact(bundleId, 'logs/errors.txt');
        return;
      }

      if (btn.dataset.action === 'submit-user-context') {
        const runId = btn.dataset.runId;
        const origin = btn.dataset.origin;
        const input = root.querySelector('[data-context-input]');
        const contextText = input ? input.value : '';
        if (!contextText.trim()) {
          toast('Missing context', 'Please enter some context before submitting.', { icon: 'exclamation-triangle' });
          return;
        }
        try {
          await postJSON('/user-context', { run_id: runId, origin, context_text: contextText });
          toast('Saved', 'Context saved. Re-running triage shortly.', { icon: 'check-circle' });
          scheduleRender();
        } catch (err) {
          toast('Error', `<div>Failed to save context</div><div class="small text-body-secondary">${escapeHtml(String(err))}</div>`, { icon: 'exclamation-triangle' });
        }
        return;
      }

      if (btn.dataset.action === 'open-artifact') {
        const relpath = btn.dataset.relpath;
        await openArtifact(bundleId, relpath);
        return;
      }

      if (btn.dataset.action === 'open-artifact-row') {
        const relpath = btn.dataset.relpath;
        await openArtifact(bundleId, relpath);
        return;
      }
    });
  }

  function renderBundleSummary(bundle, artifacts) {
    // Filter out hidden artifacts
    const visibleArtifacts = artifacts.filter((a) => !HIDDEN_ARTIFACTS.includes(a.relpath));

    const aRows = visibleArtifacts
      .slice()
      .sort((a, b) => a.relpath.localeCompare(b.relpath))
      .slice(0, 15)
      .map((a) => `<li class="list-group-item d-flex justify-content-between align-items-center"><span class="font-monospace">${escapeHtml(a.relpath)}</span>${badge(a.kind || 'unknown', 'secondary', 'badge-sm')}</li>`)
      .join('');

    return `
      <div class="row g-3">
        <div class="col-md-6">
          <div class="card">
            <div class="card-body">
              <h6 class="card-title">Bundle JSON</h6>
              <div class="code-block"><pre><code class="hljs language-json">${escapeHtml(JSON.stringify(bundle, null, 2))}</code></pre></div>
            </div>
          </div>
        </div>
        <div class="col-md-6">
          <div class="card">
            <div class="card-body">
              <h6 class="card-title">Artifacts (sample)</h6>
              ${visibleArtifacts.length ? `<ul class="list-group list-group-flush">${aRows}</ul>` : renderEmpty('No artifacts', 'Bundle has no tracked artifacts.')}
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderArtifactsList(bundleId, artifacts) {
    // Filter out hidden artifacts
    const visibleArtifacts = artifacts.filter((a) => !HIDDEN_ARTIFACTS.includes(a.relpath));

    if (!visibleArtifacts.length) return renderEmpty('No artifacts', 'No tracked artifacts for this bundle.');

    const rows = visibleArtifacts
      .slice()
      .sort((a, b) => a.relpath.localeCompare(b.relpath))
      .map((a) => {
        return `
          <tr>
            <td class="id-col">${escapeHtml(a.relpath)}</td>
            <td>${badge(a.kind || 'unknown', 'secondary', 'badge-sm')}</td>
            <td class="text-end">${escapeHtml(String(a.size ?? ''))}</td>
            <td class="text-end">
              <button class="btn btn-sm btn-outline-secondary" data-action="open-artifact-row" data-relpath="${escapeHtml(a.relpath)}">Open</button>
            </td>
          </tr>
        `;
      })
      .join('');

    return `
      <div class="table-responsive">
        <table class="table table-sm mb-0">
          <thead>
            <tr>
              <th>Relpath</th>
              <th>Kind</th>
              <th class="text-end">Size</th>
              <th class="text-end">Action</th>
            </tr>
          </thead>
          <tbody>
            ${rows}
          </tbody>
        </table>
      </div>
    `;
  }

  async function openArtifact(bundleId, relpath) {
    const url = `/bundles/${encodeURIComponent(bundleId)}/artifacts/${relpath}`;

    let text;
    try {
      text = await fetchText(url);
    } catch (e) {
      toast('Error', `<div>Failed to load artifact</div><div class="small text-body-secondary">${escapeHtml(String(e))}</div>`, { icon: 'exclamation-triangle' });
      return;
    }

    const root = $('#main-content');
    if (!root) return;

    // Decide which tab and renderer
    let tabTarget = '#tab-artifacts';
    let html = renderCode(text);

    if (relpath.endsWith('.md')) {
      tabTarget = '#tab-triage';
      html = renderMarkdown(text);
    } else if (relpath.endsWith('.diff') || relpath.endsWith('.patch')) {
      tabTarget = '#tab-patch';
      html = renderDiff(text);
    } else if (relpath.endsWith('.txt') || relpath.includes('logs/')) {
      tabTarget = '#tab-logs';
      html = renderCode(text);
    }

    // Switch tab
    const btn = root.querySelector(`[data-bs-target="${tabTarget}"]`);
    if (btn && !btn.disabled) bootstrap.Tab.getOrCreateInstance(btn).show();

    const pane = root.querySelector(tabTarget);
    if (!pane) return;

    pane.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-2">
        <div class="d-flex align-items-center gap-2">
          <button class="btn btn-sm btn-outline-secondary" data-action="back-to-artifacts">${icon('arrow-left')} Back</button>
          <span class="font-monospace">${escapeHtml(relpath)}</span>
        </div>
        <div class="d-flex gap-2">
          <a class="btn btn-sm btn-outline-secondary" href="${escapeHtml(url)}" target="_blank" rel="noopener">${icon('box-arrow-up-right')} Raw</a>
          <button class="btn btn-sm btn-outline-secondary" data-action="copy-artifact">${icon('clipboard')} Copy</button>
        </div>
      </div>
      ${html}
    `;

    highlightAll(pane);

    pane.querySelector('[data-action="copy-artifact"]')?.addEventListener('click', () => copyToClipboard(text));
    pane.querySelector('[data-action="back-to-artifacts"]')?.addEventListener('click', () => {
      // Switch to artifacts tab and re-render the artifacts list
      const artifactsBtn = root.querySelector('[data-bs-target="#tab-artifacts"]');
      if (artifactsBtn) bootstrap.Tab.getOrCreateInstance(artifactsBtn).show();
    });
  }

  // =============================================================================
  // Theme / Pause
  // =============================================================================

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    localStorage.setItem(STORAGE_KEYS.theme, theme);
  }

  function initTheme() {
    const stored = localStorage.getItem(STORAGE_KEYS.theme) || 'auto';
    applyTheme(stored);

    $all('[data-theme]').forEach((btn) => {
      btn.addEventListener('click', () => applyTheme(btn.dataset.theme));
    });
  }

  function initPause() {
    const stored = localStorage.getItem(STORAGE_KEYS.paused);
    state.paused = stored === 'true';

    const input = $('#pause-stream');
    if (input) {
      input.checked = state.paused;
      input.addEventListener('change', (e) => {
        state.paused = e.target.checked;
        localStorage.setItem(STORAGE_KEYS.paused, state.paused ? 'true' : 'false');

        if (state.paused) {
          stopSSE();
          toast('Paused', '<div>SSE stream paused</div>', { icon: 'pause-circle', delay: 2000 });
        } else {
          connectSSE();
          toast('Resumed', '<div>SSE stream resumed</div>', { icon: 'play-circle', delay: 2000 });
        }
      });
    }
  }

  // =============================================================================
  // Global handlers
  // =============================================================================

  function initGlobalHandlers() {
    window.addEventListener('hashchange', () => navigate());

    $('#close-detail')?.addEventListener('click', () => clearDetail());

    // Keep offcanvas close button in sync
    $('#detail-offcanvas')?.addEventListener('hidden.bs.offcanvas', () => {
      // no-op
    });
  }

  // =============================================================================
  // Startup
  // =============================================================================

  function bootstrapUI() {
    initTheme();
    initPause();
    initGlobalHandlers();

    // highlight.js languages registered in index.html (diff/bash/makefile)
    try { hljs.registerLanguage('diff', window.hljs?.getLanguage?.('diff') || undefined); } catch (_) {}

    // initial render
    navigate();

    // SSE connect (unless paused)
    state.connection.lastEventId = loadLastEventId();
    if (!state.paused) connectSSE();
    else setConnectionStatus('disconnected', state.connection.lastEventId);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapUI);
  } else {
    bootstrapUI();
  }
})();
