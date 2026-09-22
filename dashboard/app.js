// SPDX-License-Identifier: Apache-2.0
const state = { runs: [], query: "", status: "", cards: new Map(), firstLoad: true };
const poll = { timer: null, inflight: null, delay: 2500, min: 2500, max: 30000 };
const nativeState = { current: null, busy: true };
const nativeProviders = ["codex", "claude", "grok"];
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (n) => new Intl.NumberFormat().format(Number(n || 0));
const ratio = (n) => { const value = Number(n ?? 0); return Number.isFinite(value) ? Math.max(0, Math.min(1, value > 1 ? value / 100 : value)) : 0; };
const pct = (n) => `${Math.round(ratio(n) * 100)}%`;
const elapsed = (n) => Number(n || 0) < 60 ? `${Number(n || 0).toFixed(1)}s` : `${(Number(n || 0) / 60).toFixed(1)}m`;
const when = (iso) => { const d = new Date(iso); return isNaN(d) ? "Unknown time" : d.toLocaleString(); };

function taskHtml(task) {
  const probs = Object.entries(task.probabilities || {}).sort((a,b) => b[1]-a[1]);
  // SVG geometry attributes work under style-src 'self'; inline CSS widths do not.
  const bars = probs.map(([name,value]) => `<div class="prob"><span>${escapeHtml(name)}</span><svg class="prob-track" viewBox="0 0 100 5" preserveAspectRatio="none" aria-hidden="true"><rect class="prob-fill" width="${ratio(value)*100}" height="5" /></svg><em>${pct(value)}</em></div>`).join("");
  const usage = task.usage || {};
  return `<section class="task">
    <div class="task-top"><div><small>${escapeHtml(task.category || "unclassified")} · ${escapeHtml(task.difficulty || "unknown")}</small><h3>${escapeHtml(task.title || task.id || "Untitled task")}</h3></div><span class="badge">${escapeHtml(task.status || "unknown")}</span></div>
    <div class="route"><div><small>ROUTED TO</small><strong>${escapeHtml(task.execution_provider || task.provider || "—")}</strong></div><div><small>INHERITED MODEL</small><strong>${escapeHtml(observedModels(task))}</strong></div><div><small>EFFORT</small><strong>${escapeHtml(effortLabel(task))}</strong></div><div><small>CONFIDENCE</small><strong>${pct(task.confidence)}</strong></div></div>
    ${task.fallback ? `<p class="notice">Fallback: ${escapeHtml(task.fallback_reason || "unspecified")}</p>` : ""}
    <div class="detail-grid"><div><h4>Candidate probabilities</h4>${bars || "<p>No probability data</p>"}</div><div><h4>Execution</h4><dl><dt>Input</dt><dd>${fmt(usage.input_tokens)}</dd><dt>Output</dt><dd>${fmt(usage.output_tokens)}</dd><dt>Cached</dt><dd>${fmt(usage.cached_input_tokens)}</dd><dt>Elapsed</dt><dd>${elapsed(task.elapsed_seconds)}</dd></dl></div></div>
    ${task.result_summary ? `<section class="result"><h4>Result summary</h4><pre>${escapeHtml(task.result_summary)}</pre></section>` : ""}
  </section>`;
}

function evidenceFlowHtml(run) {
  return lineageHtml(run);
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
  card.querySelector(".run-body").innerHTML = evidenceFlowHtml(run) + (run.tasks || []).map(taskHtml).join("") + (run.has_report ? `<a class="report" href="/reports/${encodeURIComponent(run.run_id)}" target="_blank" rel="noopener">Open generated report ↗</a>` : "");
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

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

async function loadRuns(signal) {
  const data = await fetchJson("/api/runs?limit=500", { signal });
  reconcile(data.runs || []);
}

async function loadHealth(signal) {
  const health = await fetchJson("/api/health", { signal });
  $("subtitle").textContent = health.runs_dir;
}

const EFFORT_SOURCES = { jev: "jev", user_setting: "your setting" };

function effortLabel(task) {
  // The model is inherited, so effort is the only routed field: always say who chose it.
  const effort = task.effort || "unsupported";
  const source = EFFORT_SOURCES[task.effort_source] || (task.fallback ? "fallback" : "");
  const reason = task.fallback && task.fallback_reason ? `: ${task.fallback_reason}` : "";
  return source ? `${effort} (${source}${reason})` : effort;
}

function nativePayload() {
  return { native_fallbacks: Object.fromEntries(nativeProviders.map(provider =>
    [provider, { effort: $(`nativeEffort-${provider}`).value || null }])) };
}

function updateNativeState() {
  nativeProviders.forEach(provider => {
    $(`nativeEffort-${provider}`).disabled = nativeState.busy || !nativeState.current;
  });
  const changed = nativeState.current && JSON.stringify(nativePayload()) !== JSON.stringify(nativeState.current);
  $("saveNativeSettings").disabled = nativeState.busy || !changed;
  $("reloadNativeSettings").disabled = nativeState.busy;
  if (!nativeState.busy && nativeState.current) {
    $("nativeSettingsState").textContent = changed ? "Unsaved changes" : "Defaults synced";
    $("nativeSettingsState").dataset.tone = changed ? "pending" : "ready";
  }
}

function renderNativeConfig(config) {
  nativeProviders.forEach(provider => {
    const selected = config.native_fallbacks?.[provider]?.effort || "";
    const options = config.effort_options || [];
    const select = $(`nativeEffort-${provider}`);
    select.replaceChildren(new Option("No default — parent handles fallback", ""),
      ...options.map(effort => new Option(effort, effort)));
    if (selected && !options.includes(selected)) {
      select.add(new Option(`${selected} (saved; not in this config)`, selected));
    }
    select.value = selected;
    $(`nativeNote-${provider}`).textContent = options.length
      ? `Applied when Jev misses the 1s cap. The ${provider} model is inherited, never routed.`
      : "No effort levels configured. Add an efforts list to Jev's config, then reload.";
  });
  nativeState.current = nativePayload();
  nativeState.busy = false;
  updateNativeState();
}

async function loadNativeConfig() {
  // Reload only this form; do not discard unsaved provider defaults silently.
  if (nativeState.current && JSON.stringify(nativePayload()) !== JSON.stringify(nativeState.current)) {
    $("nativeSettingsMessage").textContent = "Save your changed defaults before reloading effort options.";
    return;
  }
  nativeState.busy = true;
  updateNativeState();
  try {
    renderNativeConfig(await fetchJson("/api/config"));
    $("nativeSettingsMessage").textContent = "Effort options reloaded.";
    $("nativeSettingsMessage").dataset.tone = "ready";
  } catch (error) {
    nativeState.busy = false;
    updateNativeState();
    $("nativeSettingsState").textContent = "Defaults unavailable";
    $("nativeSettingsState").dataset.tone = "error";
    $("nativeSettingsMessage").textContent = `${error.message}. Reload effort options to try again.`;
    $("nativeSettingsMessage").dataset.tone = "error";
  }
}

async function saveNativeConfig(event) {
  event.preventDefault();
  nativeState.busy = true;
  updateNativeState();
  $("nativeSettingsState").textContent = "Saving defaults...";
  $("nativeSettingsState").dataset.tone = "pending";
  try {
    renderNativeConfig(await fetchJson("/api/config", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(nativePayload()),
    }));
    $("nativeSettingsMessage").textContent = "Fallback effort saved. It applies whenever Jev misses the 1s cap.";
    $("nativeSettingsMessage").dataset.tone = "ready";
  } catch (error) {
    nativeState.busy = false;
    updateNativeState();
    $("nativeSettingsState").textContent = "Save failed";
    $("nativeSettingsState").dataset.tone = "error";
    $("nativeSettingsMessage").textContent = `${error.message}. Your edits are kept; review them and save again.`;
    $("nativeSettingsMessage").dataset.tone = "error";
  }
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
$("nativeSettingsForm").addEventListener("change", updateNativeState);
$("nativeSettingsForm").addEventListener("submit", saveNativeConfig);
$("reloadNativeSettings").addEventListener("click", loadNativeConfig);
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(true); });
Promise.allSettled([loadHealth(), loadNativeConfig()]).finally(() => tick(false));
