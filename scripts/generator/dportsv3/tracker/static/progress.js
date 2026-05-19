var SbInterval = 10;   // seconds between polls when active
var PER_PAGE   = 50;

var allRows      = [];  // raw entry objects accumulated from history files
var filteredRows = [];  // subset after filter + sort
var currentPage  = 0;
var filterText   = '';
var sortCol      = 'entry';
var sortDir      = -1;  // -1 desc, 1 asc  (newest first by default)
var kfiles       = 0;
var lastKfile    = 0;
var run_active   = false;

/* ── Utilities ── */

function $(id) { return document.getElementById(id); }

function setText(id, val) {
    var el = $(id);
    if (el) el.textContent = (val !== undefined && val !== null) ? String(val).trim() : '';
}

function esc(s) {
    if (!s) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function digit2(n) { return n > 9 ? '' + n : '0' + n; }

function fetchJSON(url) {
    return fetch(url).then(function(r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
    });
}

/* ── Loading state ── */

function setLoadingState(text) {
    var el = $('loading-state');
    if (!el) return;
    if (!text) {
        el.innerHTML = '';
    } else {
        el.innerHTML = '<span class="spinner"></span>' + text;
    }
}

/* ── Progress bar (CSS segments) ── */

function updateProgress(stats) {
    var q = stats.queued || 1;

    function setSeg(id, n) {
        var el = $(id);
        if (!el) return;
        if (!n) {
            el.style.flexGrow = 0;
            el.style.display  = 'none';
        } else {
            el.style.display  = '';
            el.style.flexGrow = n / q;
        }
    }

    setSeg('seg_built',   stats.built);
    setSeg('seg_meta',    stats.meta);
    setSeg('seg_failed',  stats.failed);
    setSeg('seg_ignored', stats.ignored);
    setSeg('seg_skipped', stats.skipped);
}

/* ── Header ── */

function updateSummary(data) {
    kfiles     = parseInt(data.kfiles) || 0;
    run_active = parseInt(data.active) !== 0;

    setText('profile', data.profile);
    setText('kickoff', data.kickoff);
    setText('polling', run_active ? 'Active' : 'Complete');

    if (data.stats) {
        var s = data.stats;
        setText('stats_queued',   s.queued);
        setText('stats_built',    s.built);
        setText('stats_meta',     s.meta);
        setText('stats_failed',   s.failed);
        setText('stats_ignored',  s.ignored);
        setText('stats_skipped',  s.skipped);
        setText('stats_remains',  s.remains);
        setText('stats_elapsed',  s.elapsed);
        setText('stats_pkghour',  s.pkghour);
        setText('stats_impulse',  s.impulse);
        setText('stats_swapinfo', s.swapinfo);

        updateLoad(s.load);
        updateProgress(s);
    }

    updateBuilders(data.builders || []);
}

function updateLoad(loadStr) {
    var el = $('stats_load');
    if (!el) return;
    el.textContent = loadStr ? String(loadStr).trim() : '';
    var v = parseFloat(loadStr) || 0;
    el.className = v >= 8 ? 'load-crit' : v >= 4 ? 'load-warn' : 'load-ok';
}

function updateBuilders(builders) {
    var tbody = document.querySelector('#builders_body tbody');
    if (!tbody) return;
    var html = '';
    for (var i = 0; i < builders.length; i++) {
        var b    = builders[i];
        var idle = (b.phase === 'Idle' || !b.phase);
        var dot  = '<span class="b-dot ' + (idle ? 'b-dot-idle' : 'b-dot-active') + '"></span>';
        html +=
            '<tr>' +
            '<td class="b' + b.ID + '" onclick="filter(\'id:' + b.ID + '\')" title="Filter builder ' + b.ID + '">' + dot + esc(b.ID) + '</td>' +
            '<td>' + esc(b.elapsed) + '</td>' +
            '<td>' + esc(b.phase)   + '</td>' +
            '<td>' + esc(b.origin)  + '</td>' +
            '<td>' + esc(b.lines)   + '</td>' +
            '</tr>';
    }
    tbody.innerHTML = html;
}

/* ── Search: advanced query parser ── */
/*
 * Supported syntax (space-separated, all terms ANDed):
 *   graphics        plain term — matches origin or result
 *   result:failed   field prefix — matches specific field
 *   origin:lang     field prefix
 *   id:03           field prefix
 */

function parseQuery(query) {
    var terms = [];
    var parts = query.trim().split(/\s+/);
    for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        if (!p) continue;
        var colon = p.indexOf(':');
        if (colon > 0) {
            terms.push({ field: p.slice(0, colon), value: p.slice(colon + 1).toLowerCase() });
        } else {
            terms.push({ field: null, value: p.toLowerCase() });
        }
    }
    return terms;
}

function matchRow(d, terms) {
    for (var i = 0; i < terms.length; i++) {
        var t = terms[i];
        var v = t.value;
        if (t.field === 'result') {
            if (d.result.toLowerCase().indexOf(v) < 0) return false;
        } else if (t.field === 'origin') {
            if (d.origin.toLowerCase().indexOf(v) < 0) return false;
        } else if (t.field === 'id') {
            if (String(d.ID).toLowerCase().indexOf(v) < 0) return false;
        } else if (t.field === 'phase') {
            var phase = (d.info || '').split(':')[0].toLowerCase();
            if (phase.indexOf(v) < 0) return false;
        } else {
            var idStr = '[' + d.ID + ']';
            if (d.origin.toLowerCase().indexOf(v) < 0 &&
                d.result.toLowerCase().indexOf(v) < 0 &&
                idStr.toLowerCase().indexOf(v) < 0) return false;
        }
    }
    return true;
}

/* ── Sort ── */

function sortBy(col) {
    if (sortCol === col) {
        sortDir = -sortDir;
    } else {
        sortCol = col;
        sortDir = (col === 'entry' || col === 'skip') ? -1 : 1;
    }
    currentPage = 0;
    sortRows();
    renderTable();
    renderPagination();
    updateSortHeaders();
}

function sortRows() {
    filteredRows.sort(function(a, b) {
        if (sortCol === 'entry' || sortCol === 'skip') {
            return sortDir * ((+a[sortCol] || 0) - (+b[sortCol] || 0));
        }
        var av = a[sortCol] || '';
        var bv = b[sortCol] || '';
        return sortDir * String(av).localeCompare(String(bv));
    });
}

function updateSortHeaders() {
    var ths = document.querySelectorAll('#report_table thead th[data-col]');
    for (var i = 0; i < ths.length; i++) {
        var th = ths[i];
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.getAttribute('data-col') === sortCol) {
            th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
        }
    }
}

/* ── Filter + render ── */

var activePreset = null;

function preset(txt, col) {
    activePreset = txt + '|' + (col || '');
    if (col) {
        sortCol = col;
        sortDir = -1;
    }
    filter(txt);
}

function filter(txt) {
    var input = $('search-input');
    if (input) input.value = txt;
    document.querySelectorAll('.preset-tag').forEach(function(el) {
        var key = el.dataset.filter + '|' + (el.dataset.sort || '');
        el.classList.toggle('active', key === activePreset);
    });
    activePreset = null;
    filterText  = txt.toLowerCase();
    currentPage = 0;
    applyFilter();
    renderTable();
    renderPagination();
}

function applyFilter() {
    var source;
    if (!filterText) {
        source = allRows;
    } else {
        var terms = parseQuery(filterText);
        source = allRows.filter(function(d) { return matchRow(d, terms); });
    }
    filteredRows = source.slice();
    sortRows();
}

var renderTimer   = null;
var lastRender    = 0;
var RENDER_INTERVAL = 500; // ms — max render frequency during loading

function doRender() {
    clearTimeout(renderTimer);
    renderTimer = null;
    lastRender  = Date.now();
    applyFilter();
    renderTable();
    renderPagination();
}

function computeSkip(d) {
    if (d.result === 'failed') {
        var p = (d.info || '').split(':');
        return parseInt(p[1]) || 0;
    }
    if (d.result === 'ignored') {
        var p = (d.info || '').split(':|:');
        return parseInt(p[1]) || 0;
    }
    return 0;
}

function addHistoryData(data) {
    for (var i = 0; i < data.length; i++) {
        var d = data[i];
        d.skip = computeSkip(d);
        allRows.push(d);
    }
    var due = RENDER_INTERVAL - (Date.now() - lastRender);
    if (due <= 0) {
        doRender();
    } else {
        clearTimeout(renderTimer);
        renderTimer = setTimeout(doRender, due);
    }
}

/* ── Table render ── */

var RESULT_GLYPHS = {
    built:   '✓',
    failed:  '✗',
    meta:    '◈',
    ignored: '⊘',
    skipped: '⇢'
};

function logfile(origin) {
    var p = origin.split('/');
    return '../' + p[0] + '___' + (p[1] || '') + '.log';
}

function infoHTML(result, origin, info) {
    if (result === 'meta')    return 'meta-node complete.';
    if (result === 'built')   return '<a href="' + logfile(origin) + '">logfile</a>';
    if (result === 'failed')  return 'Failed ' + esc((info || '').split(':')[0]) + ' phase (<a href="' + logfile(origin) + '">logfile</a>)';
    if (result === 'skipped') return 'Issue with ' + esc(info);
    if (result === 'ignored') return esc((info || '').split(':|:')[0]);
    return '';
}

function skipHTML(d) {
    if (d.result === 'failed' || d.result === 'ignored') return String(d.skip);
    return '';
}

function originLink(origin) {
    var p    = origin.split('/');
    var name = p[1] ? p[1].split('@')[0] : origin;
    return '<a href="https://www.freshports.org/' + p[0] + '/' + name + '">' + esc(origin) + '</a>';
}

function renderTable() {
    var tbody = $('report_body');
    var start = currentPage * PER_PAGE;
    var rows  = filteredRows.slice(start, start + PER_PAGE);
    var html  = '';

    for (var i = 0; i < rows.length; i++) {
        var d     = rows[i];
        var cls   = (i % 2 === 1) ? ' class="odd"' : '';
        var glyph = RESULT_GLYPHS[d.result] || '';
        html +=
            '<tr' + cls + '>' +
            '<td><span class="entry" onclick="filter(\'origin:' + esc(d.origin) + '\')" title="Filter by this port">' + d.entry + '</span></td>' +
            '<td>' + esc(d.elapsed)  + '</td>' +
            '<td>[' + esc(d.ID) + ']</td>' +
            '<td><div class="' + d.result + ' result">' + glyph + ' ' + d.result + '</div></td>' +
            '<td>' + originLink(d.origin) + '</td>' +
            '<td>' + infoHTML(d.result, d.origin, d.info) + '</td>' +
            '<td>' + skipHTML(d) + '</td>' +
            '<td>' + esc(d.duration) + '</td>' +
            '</tr>';
    }
    tbody.innerHTML = html;
    updateSortHeaders();
}

function renderPagination() {
    var total = filteredRows.length;
    var pages = Math.ceil(total / PER_PAGE);
    var info  = $('page-info');
    var prev  = $('page-prev');
    var next  = $('page-next');
    if (!info) return;

    if (total === 0) {
        info.textContent = 'No results';
    } else {
        var s = currentPage * PER_PAGE + 1;
        var e = Math.min((currentPage + 1) * PER_PAGE, total);
        info.textContent = s + '–' + e + ' of ' + total;
    }
    prev.disabled = currentPage === 0;
    next.disabled = currentPage >= pages - 1;
}

function setPerPage(n) {
    PER_PAGE    = parseInt(n);
    currentPage = 0;
    renderTable();
    renderPagination();
}

function prevPage() {
    if (currentPage > 0) {
        currentPage--;
        renderTable();
        renderPagination();
    }
}

function nextPage() {
    if (currentPage < Math.ceil(filteredRows.length / PER_PAGE) - 1) {
        currentPage++;
        renderTable();
        renderPagination();
    }
}

/* ── Fetch loop ── */

var historyRunning = false;

async function loadNewHistory() {
    if (historyRunning) return;
    historyRunning = true;

    var total = kfiles;
    var start = lastKfile + 1;

    if (start > total) { historyRunning = false; return; }

    var count = total - start + 1;
    setLoadingState('Loading build logs (' + count + ' file' + (count > 1 ? 's' : '') + ')');

    var keys    = [];
    var fetches = [];
    for (var k = start; k <= total; k++) {
        keys.push(k);
        fetches.push(fetchJSON(digit2(k) + '_history.json').catch(function() { return null; }));
    }

    var results = await Promise.all(fetches);

    for (var i = 0; i < results.length; i++) {
        if (results[i] === null) break; // gap or not-yet-written file — stop, retry next poll
        addHistoryData(results[i]);
        lastKfile = keys[i];
    }

    setLoadingState('');
    historyRunning = false;
}

async function pollSummary() {
    try {
        var data = await fetchJSON('summary.json');
        updateSummary(data);
        loadNewHistory(); // fire-and-forget: runs concurrently with next summary poll
        if (run_active) {
            setTimeout(pollSummary, SbInterval * 1000);
        } else {
            var zone = $('builders_zone_2');
            if (zone) zone.style.display = 'none';
        }
    } catch (e) {
        setTimeout(pollSummary, SbInterval * 500);
    }
}

document.addEventListener('DOMContentLoaded', pollSummary);

document.addEventListener('keydown', function(e) {
    if (e.key === 'F5') e.preventDefault();
});
