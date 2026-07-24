// Bundle-detail page behaviour: operator actions, delivery status, and
// the fix-review chat. Each IIFE self-guards on its section's presence,
// so it's a no-op when that section isn't rendered. Server-injected
// values arrive via window.DP_BUNDLE (bootstrap in agentic_bundle.html).

// --- Operator actions ---
(function () {
  const statusEl = document.getElementById('op-status');
  if (!statusEl) { return; }
  const initialVerificationStatus = (window.DP_BUNDLE || {}).verificationStatus;
  const allButtons = () => document.querySelectorAll('button[id^="op-"]');

  function disableAll() {
    allButtons().forEach(b => { b.disabled = true; });
  }
  function restorePreClickEnabled(preState) {
    allButtons().forEach(b => { b.disabled = preState[b.id]; });
  }
  function snapshotEnabled() {
    const s = {};
    allButtons().forEach(b => { s[b.id] = b.disabled; });
    return s;
  }

  async function dpOpAction(action, bundleId) {
    const preState = snapshotEnabled();
    let body = {};
    if (action === 'verify') {
      // Env comes from the inline <select> next to the Verify
      // button — no more prompt(). The select is server-
      // populated and pre-selects the active env, so the
      // common case is "click Verify and go".
      const sel = document.getElementById('op-verify-env');
      const env = sel ? sel.value : '';
      if (!env) {
        statusEl.textContent = 'pick a dev-env to verify in';
        return;
      }
      body = {env: env};
    } else if (action === 'reject') {
      const reason = prompt('Rejection reason (will be passed to next triage as user_context):', '');
      if (!reason) { return; }
      body = {reason: reason};
    } else if (action === 'take-over') {
      const reason = prompt(
        'Take-over reason (visible in activity log + skip-flag forensics):',
        'operator is fixing this manually'
      );
      if (reason === null) { return; }  // explicit cancel
      body = {reason: reason || 'operator take-over'};
    } else if (action === 'discard') {
      const reason = prompt(
        'Discard reason (required; explains why this bundle is being walked away from):',
        ''
      );
      if (!reason) { return; }
      const skipOrigin = confirm(
        'Also lock the (target, origin) so the runner stops trying ' +
        'this port? OK = lock (default), Cancel = discard only this bundle.'
      );
      body = {reason: reason, skip_origin: skipOrigin};
    } else if (action === 'retry') {
      // Retry no longer uses prompt() — show the inline textarea
      // form instead. dpRetryShowForm handles the rest of the
      // flow (textarea → Send button → POST). Bail out of the
      // standard dpOpAction path here.
      dpRetryShowForm();
      return;
    } else if (action === 'release') {
      const releaseReason = prompt(
        'Release reason (required; explains why the operator stake is ending):',
        ''
      );
      if (!releaseReason) { return; }
      body = {reason: releaseReason};
    } else if (action === 'reopen') {
      // Gating modal: reopen is a true undo of a prior operator
      // decision, so require an explicit confirm before the
      // reason prompt.
      if (!confirm(
        'Reopen this bundle? This undoes the prior accept / reject / ' +
        'discard so the bundle is actionable again. If it was discarded ' +
        'with an origin skip lock owned by this bundle, the lock will ' +
        'also be cleared.'
      )) { return; }
      const reopenReason = prompt(
        'Reason for reopening (required; audit trail):',
        ''
      );
      if (!reopenReason) { return; }
      body = {reason: reopenReason};
    }
    // Disable all buttons immediately so a double-click can't
    // enqueue a second verify (or fire accept while verify is
    // in-flight). Re-enable only on error; on success the
    // reload re-renders with fresh state-derived enables.
    disableAll();
    statusEl.textContent = action + ' in progress…';

    let resp, data;
    try {
      resp = await fetch(
        '/api/bundles/' + encodeURIComponent(bundleId) + '/' + action,
        {method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify(body)}
      );
      data = await resp.json();
    } catch (exc) {
      statusEl.textContent = 'error: ' + exc;
      restorePreClickEnabled(preState);
      return;
    }
    if (!resp.ok) {
      statusEl.textContent = 'error: ' + (data.detail || resp.status);
      restorePreClickEnabled(preState);
      return;
    }
    if (action === 'verify') {
      // Verify is async — the runner picks the job up, runs
      // dsynth (minutes), then POSTs back to /verification.
      // Poll the bundle until verification_status changes.
      // Buttons stay disabled the whole time.
      statusEl.textContent =
        'verify enqueued as ' + (data.job_id || 'job') +
        ' — polling for result (this can take minutes)…';
      pollForVerificationChange(bundleId);
    } else if (action === 'accept') {
      // Visibility plan: Accept used to silently reload, hiding
      // the delivery outcome the server reports inline. Now we
      // surface (status, url, error) in op-status. On
      // create_failed we hold the message and skip the reload
      // so the operator actually sees the error stick; on
      // skipped we name the skip_reason for the same reason.
      // On created/updated we link the URL briefly before
      // reloading to show the Delivery card.
      const d = data.delivery || {};
      if (d.status === 'created' || d.status === 'updated') {
        const where = d.url
          ? ' → ' + d.url
          : (d.provider ? ' (' + d.provider + ')' : '');
        statusEl.innerHTML =
          'accepted — delivery ' + d.status + where +
          ' — reloading';
        setTimeout(() => location.reload(), 1200);
      } else if (d.status === 'skipped') {
        statusEl.textContent =
          'accepted — delivery skipped: ' +
          (d.skip_reason || 'unknown') + ' — reloading';
        setTimeout(() => location.reload(), 1500);
      } else if (d.status === 'create_failed') {
        statusEl.textContent =
          'accepted — delivery FAILED: ' +
          (d.error || 'unspecified error');
        // No reload — the error sticks until the operator
        // reads it and reacts. The bundle row already moved
        // to accepted so reloading would still show the
        // accept; but the toast is the only place this
        // particular error message lives in the UI.
        // Re-enable buttons so the operator can act on the
        // bundle (Reopen, etc.) without a manual page refresh.
        restorePreClickEnabled(preState);
      } else {
        statusEl.textContent =
          'accepted — delivery: ' +
          (d.status || 'unknown') + ' — reloading';
        setTimeout(() => location.reload(), 800);
      }
    } else {
      statusEl.textContent = action + ' OK — reloading';
      setTimeout(() => location.reload(), 400);
    }
  }

  function pollForVerificationChange(bundleId) {
    // Verify runs on the runner (minutes); watch the bundle until its
    // verification_status changes, then reload. Keeps watching even when
    // the tab is hidden — the operator wants the verdict regardless.
    const startedAt = Date.now();
    const maxMs = 30 * 60 * 1000;  // 30 min ceiling
    window.dpLive({
      intervalMs: 5000,
      backoffStep: 2000,
      maxIntervalMs: 15000,
      pauseWhenHidden: false,
      url: function () { return '/api/bundles/' + encodeURIComponent(bundleId); },
      onData: function (d) {
        if (Date.now() - startedAt >= maxMs) {
          statusEl.textContent =
            'verify still pending after 30 min — reload manually to check ' +
            'runner logs / activity for errors';
          allButtons().forEach(b => { b.disabled = false; });
          return 'stop';
        }
        if (d.verification_status &&
            d.verification_status !== initialVerificationStatus) {
          const verdict = d.verification_status === 'verified'
            ? 'PASSED' : 'FAILED';
          statusEl.textContent =
            'verify ' + verdict + ' (' + d.verification_status + ') — reloading';
          setTimeout(() => location.reload(), 600);
          return 'stop';
        }
      },
    }).start(5000);
  }

  document.querySelectorAll('button[id^="op-"]').forEach(btn => {
    btn.addEventListener('click', () => {
      if (btn.disabled) return;
      const action = btn.id.replace('op-', '');
      dpOpAction(action, btn.dataset.bundle);
    });
  });

  // Step 28c inline-form: replaces the prompt() that the other
  // operator actions still use. Retry context is the only field
  // that can be substantively multi-line (up to 8000 chars), so
  // it gets a proper textarea + Send/Cancel surface.
  const retryForm = document.getElementById('op-retry-form');
  const retryText = document.getElementById('op-retry-text');
  const retrySend = document.getElementById('retry-form-send');
  const retryCancel = document.getElementById('retry-form-cancel');
  const retryCharcount = document.getElementById('retry-form-charcount');

  function dpRetryShowForm() {
    if (!retryForm) return;
    retryForm.style.display = 'block';
    retryText.focus();
  }
  function dpRetryHideForm() {
    if (!retryForm) return;
    retryForm.style.display = 'none';
    retryText.value = '';
    if (retryCharcount) retryCharcount.textContent = '0 / 8000';
  }
  if (retryText && retryCharcount) {
    retryText.addEventListener('input', () => {
      retryCharcount.textContent = retryText.value.length + ' / 8000';
    });
  }
  if (retryCancel) {
    retryCancel.addEventListener('click', () => dpRetryHideForm());
  }
  if (retrySend && retryForm) {
    retrySend.addEventListener('click', async () => {
      const bundleId = retryForm.dataset.bundle;
      const text = (retryText.value || '').trim();
      if (!text) {
        statusEl.textContent = 'context is required';
        return;
      }
      retrySend.disabled = true;
      retryCancel.disabled = true;
      statusEl.textContent = 'sending retry…';
      let resp, data;
      try {
        resp = await fetch(
          '/api/bundles/' + encodeURIComponent(bundleId) + '/retry',
          {method: 'POST',
           headers: {'Content-Type': 'application/json'},
           body: JSON.stringify({context: text})}
        );
        data = await resp.json();
      } catch (exc) {
        statusEl.textContent = 'error: ' + exc;
        retrySend.disabled = false;
        retryCancel.disabled = false;
        return;
      }
      if (!resp.ok) {
        statusEl.textContent = 'error: ' + (data.detail || resp.status);
        retrySend.disabled = false;
        retryCancel.disabled = false;
        return;
      }
      statusEl.textContent = 'retry queued — reloading';
      setTimeout(() => location.reload(), 400);
    });
  }
})();

// --- Delivery mark-merged / mark-closed ---
(function () {
  const statusEl = document.getElementById('op-delivery-status');
  if (!statusEl) { return; }
  async function dpMarkStatus(newStatus, bundleId) {
    const note = prompt(
      'Optional note (short context for the audit log; can be empty):',
      ''
    );
    if (note === null) { return; }  // explicit cancel
    const body = {status: newStatus};
    if (note) { body.note = note; }
    statusEl.textContent = 'updating…';
    const merged = document.getElementById('op-mark-merged');
    const closed = document.getElementById('op-mark-closed');
    merged.disabled = true; closed.disabled = true;
    let resp, data;
    try {
      resp = await fetch(
        '/api/bundles/' + encodeURIComponent(bundleId) +
        '/delivery/status',
        {method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify(body)}
      );
      data = await resp.json();
    } catch (exc) {
      statusEl.textContent = 'error: ' + exc;
      merged.disabled = false; closed.disabled = false;
      return;
    }
    if (!resp.ok) {
      statusEl.textContent = 'error: ' + (data.detail || resp.status);
      merged.disabled = false; closed.disabled = false;
      return;
    }
    statusEl.textContent = newStatus + ' — reloading';
    setTimeout(() => location.reload(), 400);
  }
  document.getElementById('op-mark-merged').addEventListener(
    'click', (e) => dpMarkStatus('merged', e.target.dataset.bundle)
  );
  document.getElementById('op-mark-closed').addEventListener(
    'click', (e) => dpMarkStatus('closed', e.target.dataset.bundle)
  );
})();

// --- Fix-review chat ---
(function () {
  const bundleId = (window.DP_BUNDLE || {}).bundleId;
  const sessionRelpath = (window.DP_BUNDLE || {}).chatSessionRelpath;
  const logEl = document.getElementById('chat-log');
  if (!logEl) { return; }
  const inputEl = document.getElementById('chat-input');
  const sendBtn = document.getElementById('chat-send');
  const statusEl = document.getElementById('chat-status');
  // Client-held history, mirrored to localStorage keyed by bundle so a
  // reload restores the conversation (the server persists nothing). The
  // assistant entries also cache their server-rendered HTML so restore
  // doesn't need to re-call the model.
  const STORE_KEY = 'dp_chat_' + bundleId;
  const history = [];

  function save() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify(history)); } catch (e) {}
  }

  function addMsg(role, content, html) {
    const wrap = document.createElement('div');
    wrap.className = 'chat-msg ' + role;
    const label = document.createElement('div');
    label.className = 'chat-role';
    label.textContent = role === 'user' ? 'you'
      : (role === 'error' ? 'error' : 'agent');
    const body = document.createElement('div');
    // Assistant replies come pre-rendered (server-side, escaped) so
    // Markdown shows properly; user/error text is inserted as plain
    // text — never render operator input as HTML.
    if (role === 'assistant' && html) {
      body.className = 'artifact-markdown';
      body.innerHTML = html;
    } else {
      body.textContent = content;
    }
    wrap.appendChild(label);
    wrap.appendChild(body);
    logEl.appendChild(wrap);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function restore() {
    let saved;
    try { saved = JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); }
    catch (e) { saved = []; }
    if (!Array.isArray(saved)) { return; }
    saved.forEach(function (m) {
      addMsg(m.role, m.content, m.html);
      history.push(m);
    });
  }

  async function send() {
    const text = inputEl.value.trim();
    if (!text) { return; }
    inputEl.value = '';
    addMsg('user', text);
    history.push({role: 'user', content: text});
    sendBtn.disabled = true;
    inputEl.disabled = true;
    statusEl.textContent = 'thinking…';
    let resp, data;
    try {
      resp = await fetch(
        '/api/bundles/' + encodeURIComponent(bundleId) + '/chat',
        {method: 'POST',
         headers: {'Content-Type': 'application/json'},
         body: JSON.stringify({
          messages: history.map(m => ({role: m.role, content: m.content})),
          session_relpath: sessionRelpath,
        })}
      );
      data = await resp.json();
    } catch (exc) {
      addMsg('error', 'request failed: ' + exc);
      history.pop();  // let the operator retry the same question
      statusEl.textContent = '';
      sendBtn.disabled = false; inputEl.disabled = false; inputEl.focus();
      return;
    }
    if (!resp.ok) {
      addMsg('error', (data && data.detail) ? data.detail : ('error ' + resp.status));
      history.pop();
      statusEl.textContent = '';
      sendBtn.disabled = false; inputEl.disabled = false; inputEl.focus();
      return;
    }
    const reply = data.reply || '(empty reply)';
    addMsg('assistant', reply, data.reply_html);
    history.push({role: 'assistant', content: reply, html: data.reply_html});
    save();
    const u = data.usage || {};
    const consulted = (data.artifacts_included || []).length
      ? (' · consulted: ' + data.artifacts_included.join(', '))
      : '';
    statusEl.textContent = (u.total_tokens
      ? ('last turn: ' + u.total_tokens.toLocaleString() + ' tokens')
      : '') + consulted;
    sendBtn.disabled = false; inputEl.disabled = false; inputEl.focus();
  }

  sendBtn.addEventListener('click', send);
  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });

  const clearBtn = document.getElementById('chat-clear');
  if (clearBtn) {
    clearBtn.addEventListener('click', (e) => {
      e.preventDefault();
      if (!history.length) { return; }
      if (!confirm('Clear this fix-review conversation?')) { return; }
      try { localStorage.removeItem(STORE_KEY); } catch (err) {}
      history.length = 0;
      logEl.innerHTML = '';
      statusEl.textContent = '';
      inputEl.focus();
    });
  }

  // Restore any saved conversation for this bundle so a reload doesn't
  // lose it.
  restore();
})();
