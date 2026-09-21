// SPDX-License-Identifier: Apache-2.0
const state = { runs: [], query: "", status: "" };
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
    ${task.result_summary ? `<details><summary>Result summary</summary><pre>${escapeHtml(task.result_summary)}</pre></details>` : ""}
  </section>`;
}

function render() {
  const query = state.query.toLowerCase();
  const filtered = state.runs.filter(run => {
    const haystack = JSON.stringify(run).toLowerCase();
    return (!query || haystack.includes(query)) && (!state.status || run.status === state.status);
  });
  const host = $("runs"); host.innerHTML = "";
  if (!filtered.length) { host.innerHTML = `<div class="empty">No matching routing runs.</div>`; return; }
  filtered.forEach(run => {
    const node = $("runTemplate").content.cloneNode(true);
    const card = node.querySelector(".run-card");
    card.dataset.status = run.status || "unknown";
    node.querySelector(".run-title").textContent = `${run.run_id} · ${run.task_count} task${run.task_count === 1 ? "" : "s"}`;
    node.querySelector(".run-time").textContent = `${when(run.started_at)} · ${elapsed(run.elapsed_seconds)}`;
    const body = node.querySelector(".run-body");
    body.innerHTML = (run.tasks || []).map(taskHtml).join("") + (run.has_report ? `<a class="report" href="/reports/${encodeURIComponent(run.run_id)}" target="_blank" rel="noopener">Open generated report ↗</a>` : "");
    const button = node.querySelector(".run-head");
    button.addEventListener("click", () => { const open = button.getAttribute("aria-expanded") === "true"; button.setAttribute("aria-expanded", String(!open)); body.hidden = open; });
    host.appendChild(node);
  });
}

async function load() {
  $("error").hidden = true;
  try {
    const [health, data] = await Promise.all([fetch("/api/health").then(r=>r.json()), fetch("/api/runs?limit=500").then(r=>r.json())]);
    state.runs = data.runs || [];
    $("subtitle").textContent = health.runs_dir;
    $("runCount").textContent = fmt(state.runs.length);
    const tasks = state.runs.flatMap(r => r.tasks || []);
    $("taskCount").textContent = fmt(tasks.length);
    const successes = tasks.filter(t => t.status === "completed" || t.status === "success").length;
    $("successRate").textContent = tasks.length ? pct(successes / tasks.length) : "—";
    $("tokenCount").textContent = fmt(state.runs.reduce((sum,r) => sum + Number(r.router_usage?.total_tokens || 0), 0));
    const statuses = [...new Set(state.runs.map(r => r.status).filter(Boolean))].sort();
    $("status").innerHTML = `<option value="">All statuses</option>` + statuses.map(s => `<option>${escapeHtml(s)}</option>`).join("");
    render();
  } catch (error) { $("error").textContent = `Could not load routing history: ${error.message}`; $("error").hidden = false; }
}

$("search").addEventListener("input", e => { state.query = e.target.value; render(); });
$("status").addEventListener("change", e => { state.status = e.target.value; render(); });
$("refresh").addEventListener("click", load);
load();
setInterval(load, 10000);
