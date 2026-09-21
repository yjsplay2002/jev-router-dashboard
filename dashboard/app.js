// SPDX-License-Identifier: Apache-2.0
const state = { runs: [], query: "", status: "", cards: new Map(), firstLoad: true };
const poll = { timer: null, inflight: null, delay: 2500, min: 2500, max: 30000 };
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (n) => new Intl.NumberFormat().format(Number(n || 0));
const pct = (n) => `${Math.round(Number(n || 0) * 100)}%`;
const elapsed = (n) => Number(n || 0) < 60 ? `${Number(n || 0).toFixed(1)}s` : `${(Number(n || 0) / 60).toFixed(1)}m`;
const when = (iso) => { const d = new Date(iso); return isNaN(d) ? "Unknown time" : d.toLocaleString(); };

function taskHtml(task) {
  const probs = Object.entries(task.probabilities || {}).sort((a,b) => b[1]-a[1]);
  const bars = probs.map(([name,value]) => `<div class="prob"><span>${escapeHtml(name)}</span><i><b style="width:${Math.min(100, value*100)}%"></b></i><em>${pct(value)}</em></div>`).join("");
  const usage = task.usage || {};
  return `<section class="task">
    <div class="task-top"><div><small>${escapeHtml(task.category || "unclassified")} · ${escapeHtml(task.difficulty || "unknown")}</small><h3>${escapeHtml(task.title || task.id || "Untitled task")}</h3></div><span class="badge">${escapeHtml(task.status || "unknown")}</span></div>
    <div class="route"><div><small>ROUTED TO</small><strong>${escapeHtml(task.selected_by_jev || task.provider || "—")}</strong></div><div><small>OBSERVED MODEL</small><strong>${escapeHtml(task.actual_model || task.requested_model || "unknown")}</strong></div><div><small>EFFORT</small><strong>${escapeHtml(task.effort || "unknown")}</strong></div><div><small>CONFIDENCE</small><strong>${pct(task.confidence)}</strong></div></div>
    ${task.fallback ? `<p class="notice">Fallback: ${escapeHtml(task.fallback_reason || "unspecified")}</p>` : ""}
    <div class="detail-grid"><div><h4>Model selection</h4>${bars || "<p>No probability data</p>"}</div><div><h4>Execution</h4><dl><dt>Input</dt><dd>${fmt(usage.input_tokens)}</dd><dt>Output</dt><dd>${fmt(usage.output_tokens)}</dd><dt>Cached</dt><dd>${fmt(usage.cached_input_tokens)}</dd><dt>Elapsed</dt><dd>${elapsed(task.elapsed_seconds)}</dd></dl></div></div>
    ${task.result_summary ? `<section class="result"><h4>Result summary</h4><pre>${escapeHtml(task.result_summary)}</pre></section>` : ""}
  </section>`;
}

function createCard(run) {
  const fragment = $("runTemplate").content.cloneNode(true);
  const card = fragment.querySelector(".run-card");
  card.dataset.runId = run.run_id;
  updateCard(card, run);
  return card;
}

function updateCard(card, run) {
  card.dataset.status = run.status || "unknown";
  card.dataset.signature = run.source_mtime || `${run.status}:${run.elapsed_seconds}`;
  card.dataset.search = JSON.stringify(run).toLowerCase();
  card.querySelector(".run-title").textContent = `${run.run_id} · ${run.task_count} task${run.task_count === 1 ? "" : "s"}`;
  card.querySelector(".run-time").textContent = `${when(run.started_at)} · ${elapsed(run.elapsed_seconds)}`;
  card.querySelector(".run-body").innerHTML = (run.tasks || []).map(taskHtml).join("") + (run.has_report ? `<a class="report" href="/reports/${encodeURIComponent(run.run_id)}" target="_blank" rel="noopener">Open generated report ↗</a>` : "");
}

function withStableViewport(mutate) {
  const host = $("runs");
  const preservePosition = host.getBoundingClientRect().top < 0;
  const anchor = [...host.querySelectorAll(".run-card")].find(card => card.getBoundingClientRect().bottom > 0);
  const top = anchor?.getBoundingClientRect().top;
  mutate();
  if (preservePosition && anchor?.isConnected && Number.isFinite(top)) window.scrollBy(0, anchor.getBoundingClientRect().top - top);
}

function applyFilters() {
  const query = state.query.toLowerCase();
  let visible = 0;
  state.cards.forEach((card, runId) => {
    const run = state.runs.find(item => item.run_id === runId);
    const show = Boolean(run) && (!query || card.dataset.search.includes(query)) && (!state.status || run.status === state.status);
    card.hidden = !show;
    if (show) visible += 1;
  });
  let empty = $("emptyState");
  if (!visible) {
    if (!empty) {
      empty = document.createElement("div");
      empty.id = "emptyState";
      empty.className = "empty";
      empty.textContent = "No matching routing runs.";
      $("runs").appendChild(empty);
    }
  } else empty?.remove();
}

function reconcile(runs) {
  const host = $("runs");
  const previousIds = new Set(state.runs.map(run => run.run_id));
  const nextIds = new Set(runs.map(run => run.run_id));
  let changed = 0;
  withStableViewport(() => {
    state.cards.forEach((card, runId) => {
      if (!nextIds.has(runId)) { card.remove(); state.cards.delete(runId); }
    });
    runs.forEach((run, index) => {
      let card = state.cards.get(run.run_id);
      if (!card) {
        card = createCard(run);
        state.cards.set(run.run_id, card);
        changed += 1;
      } else if (card.dataset.signature !== (run.source_mtime || `${run.status}:${run.elapsed_seconds}`)) {
        updateCard(card, run);
        changed += 1;
      }
      const expected = host.children[index];
      if (expected !== card) host.insertBefore(card, expected || null);
    });
  });
  state.runs = runs;
  updateStatusOptions();
  updateStats();
  applyFilters();
  const added = runs.filter(run => !previousIds.has(run.run_id)).length;
  if (!state.firstLoad && (added || changed)) {
    $("announce").textContent = `${added ? `${added} new run${added === 1 ? "" : "s"}. ` : ""}${changed} routing record${changed === 1 ? "" : "s"} updated.`;
  }
  state.firstLoad = false;
}

function updateStatusOptions() {
  const select = $("status");
  const statuses = [...new Set(state.runs.map(run => run.status).filter(Boolean))].sort();
  const signature = statuses.join("|");
  if (select.dataset.signature === signature) return;
  const selected = state.status;
  select.replaceChildren(new Option("All statuses", ""), ...statuses.map(status => new Option(status, status)));
  select.value = selected;
  select.dataset.signature = signature;
}

function updateStats() {
  const tasks = state.runs.flatMap(run => run.tasks || []);
  const successes = tasks.filter(task => task.status === "completed" || task.status === "success").length;
  const values = {
    runCount: fmt(state.runs.length), taskCount: fmt(tasks.length),
    successRate: tasks.length ? pct(successes / tasks.length) : "—",
    tokenCount: fmt(state.runs.reduce((sum,run) => sum + Number(run.router_usage?.total_tokens || 0), 0)),
  };
  Object.entries(values).forEach(([id, value]) => { if ($(id).textContent !== value) $(id).textContent = value; });
}

async function fetchJson(url, signal) {
  const response = await fetch(url, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadRuns(signal) {
  const data = await fetchJson("/api/runs?limit=500", signal);
  reconcile(data.runs || []);
}

async function loadHealth(signal) {
  const health = await fetchJson("/api/health", signal);
  $("subtitle").textContent = health.runs_dir;
}

function schedule() {
  clearTimeout(poll.timer);
  poll.timer = setTimeout(() => tick(false), poll.delay);
}

function tick(manual = false) {
  if (poll.inflight) return poll.inflight;
  if (document.hidden && !manual) { schedule(); return Promise.resolve(); }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  $("refresh").disabled = true;
  poll.inflight = Promise.all([loadRuns(controller.signal), manual ? loadHealth(controller.signal) : Promise.resolve()])
    .then(() => { poll.delay = poll.min; $("error").hidden = true; })
    .catch(error => {
      poll.delay = Math.min(poll.max, poll.delay * 2);
      $("error").textContent = error.name === "AbortError" ? "Live update timed out. Retrying automatically." : `Live update failed: ${error.message}. Retrying automatically.`;
      $("error").hidden = false;
    })
    .finally(() => {
      clearTimeout(timeout);
      poll.inflight = null;
      $("refresh").disabled = false;
      schedule();
    });
  return poll.inflight;
}

$("search").addEventListener("input", event => { state.query = event.target.value; applyFilters(); });
$("status").addEventListener("change", event => { state.status = event.target.value; applyFilters(); });
$("refresh").addEventListener("click", () => tick(true));
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(true); });
loadHealth().catch(() => {}).finally(() => tick(false));
