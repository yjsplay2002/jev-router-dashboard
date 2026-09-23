// SPDX-License-Identifier: Apache-2.0
// The shift log: every prompt is a gear change. The hook selects the gear, the proxy engages it.
const state = { runs: [], efforts: ["low", "medium", "high"], query: "", provider: "", status: "", rows: new Map(), firstLoad: true, plateRun: null };
const poll = { timer: null, inflight: null, delay: 2500, min: 2500, max: 30000 };
const nativeState = { current: null, busy: true };
const nativeProviders = ["codex", "claude", "grok"];
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = (n) => new Intl.NumberFormat().format(Number(n || 0));
const ratio = (n) => { const value = Number(n ?? 0); return Number.isFinite(value) ? Math.max(0, Math.min(1, value > 1 ? value / 100 : value)) : 0; };
const pct = (n) => `${Math.round(ratio(n) * 100)}%`;
const secs = (n) => `${Number(n || 0).toFixed(2)}s`;
const SVG = "http://www.w3.org/2000/svg";
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

function clock(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return { time: "—", day: "" };
  const today = new Date().toDateString() === d.toDateString();
  return { time: d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
           day: today ? "today" : d.toLocaleDateString([], { month: "short", day: "numeric" }) };
}

function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (!Number.isFinite(s)) return "";
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
}

const short = (effort) => ({ low: "L", medium: "M", high: "H", xhigh: "XH", max: "MX", minimal: "MN" }[effort] || String(effort || "?").slice(0, 2).toUpperCase());
const isTurn = (run) => run.mode === "turn_effort";
const sessionOf = (run) => (String(run.run_id).match(/-effort-([0-9a-z]+)$/) || [])[1] || "";

// One run's shift: which gear, and whether it was engaged on the wire.
function shift(run) {
  const task = run.tasks?.[0] || {};
  if (!isTurn(run)) return { kind: "delegated", gear: task.effort || "", task, label: "Delegated run", note: `${run.task_count} task${run.task_count === 1 ? "" : "s"}` };
  const proxy = run.proxy || { requests: 0, changed: 0 };
  if (task.fallback) return { kind: "fallback", gear: task.effort, task, proxy, label: "Fallback · N", note: task.fallback_reason || "Jev did not answer" };
  if (proxy.requests > 0) {
    const kept = proxy.requests - proxy.changed;
    return { kind: "engaged", gear: task.effort, task, proxy, label: `Engaged · ${proxy.requests} req`,
             note: proxy.changed ? `${proxy.changed} shifted${kept ? `, ${kept} already there` : ""}` : "host was already in this gear" };
  }
  return { kind: "selected", gear: task.effort, task, proxy, label: "Selected, not engaged", note: "no request passed the proxy" };
}

/* ── the gate plate ─────────────────────────────────── */
function gateGeometry(efforts) {
  const n = Math.max(1, efforts.length), x0 = 50, x1 = 310, top = 40, bottom = 180, rail = 110;
  const xs = efforts.map((_, i) => n === 1 ? (x0 + x1) / 2 : x0 + ((x1 - x0) * i) / (n - 1));
  const slots = efforts.map((effort, i) => ({ effort, x: xs[i], y: i % 2 === 0 ? top : bottom }));
  // neutral sits on the rail between the first two slots, clear of every slot mouth
  const nx = n > 1 ? (xs[0] + xs[1]) / 2 : xs[0] - 40;
  return { slots, rail, x0: Math.min(xs[0], nx), x1: xs[xs.length - 1], nx };
}

function el(name, attrs = {}, text) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

function drawGate(run, info) {
  const svg = $("gate");
  const { slots, rail, x0, x1, nx } = gateGeometry(state.efforts);
  const path = `M${x0} ${rail} H${x1} ` + slots.map(s => `M${s.x} ${rail} V${s.y}`).join(" ");
  const current = info && info.kind !== "fallback" ? slots.find(s => s.effort === info.gear) : null;
  const fallbackSlot = info?.kind === "fallback" ? slots.find(s => s.effort === info.gear) : null;
  const target = current ? { x: current.x, y: current.y } : { x: nx, y: rail };
  const title = svg.querySelector("title");
  title.textContent = info ? `Shift gate: ${info.kind === "fallback" ? `neutral, host default ${info.gear}` : `${info.gear}, ${info.label}`}` : "Shift gate: no turn yet";
  svg.replaceChildren(title,
    el("path", { d: path, class: "slot-bevel" }), el("path", { d: path, class: "slot" }),
    el("text", { x: nx, y: rail + 32, class: "n-label" }, "N"),
    ...slots.map(s => el("text", { x: s.x, y: s.y < rail ? s.y - 24 : s.y + 38, class: `gear-label${current === s ? " is-current" : ""}` }, s.effort)),
    ...(fallbackSlot ? [el("circle", { cx: fallbackSlot.x, cy: fallbackSlot.y, r: 15, class: "ghost" })] : []));
  svg.dataset.state = info?.kind || "none";
  const knob = el("g", { class: "knob-group" });
  knob.append(el("circle", { r: 20, class: "knob" }), el("circle", { r: 13, class: "knob-cap" }),
              el("text", { class: "knob-letter", y: 1 }, info ? (current ? short(info.gear) : "N") : ""));
  svg.append(knob);
  moveKnob(knob, target, rail, run);
}

// The one authored motion: the knob leaves neutral, runs the rail, drops into the slot, overshoots, settles.
function moveKnob(knob, target, rail, run) {
  const place = (p) => knob.setAttribute("transform", `translate(${p.x} ${p.y})`);
  const previous = state.knobAt;
  state.knobAt = target;
  const isNew = run && state.plateRun && state.plateRun !== run.run_id;
  state.plateRun = run?.run_id || null;
  if (!previous || !isNew || reduceMotion.matches || typeof knob.animate !== "function") { place(target); return; }
  place(target);
  const dy = target.y - rail, sign = Math.sign(dy) || 1;
  const frames = [
    { transform: `translate(${previous.x}px, ${previous.y}px)` },
    { transform: `translate(${previous.x}px, ${rail}px)`, offset: 0.25 },
    { transform: `translate(${target.x}px, ${rail}px)`, offset: 0.6 },
    { transform: `translate(${target.x}px, ${target.y + sign * 6}px)`, offset: 0.85 },
    { transform: `translate(${target.x}px, ${target.y}px)` },
  ];
  knob.removeAttribute("transform");
  knob.animate(frames, { duration: 700, easing: "cubic-bezier(.2,.8,.2,1)" }).finished
    .then(() => place(target)).catch(() => place(target));
}

function drawPlate() {
  const run = state.runs.find(isTurn);
  const info = run ? shift(run) : null;
  drawGate(run, info);
  if (!run) {
    $("gearName").textContent = "—";
    $("engagement").dataset.state = "none";
    $("engagement").textContent = "No turn recorded yet. The hook writes one per prompt.";
    $("readoutFacts").replaceChildren(); $("readoutPrompt").textContent = ""; $("ratio").replaceChildren(); $("ratioLegend").textContent = "";
    return;
  }
  const task = info.task;
  $("gearName").textContent = info.kind === "fallback" ? `N · ${info.gear || "default"}` : info.gear || "—";
  $("engagement").dataset.state = info.kind;
  $("engagement").innerHTML = `${escapeHtml(info.label)} <small>${escapeHtml(info.note)}</small>`;
  const facts = [
    ["Host", run.origin_provider || "—"], ["Session", sessionOf(run) || "—"],
    ["When", ago(run.started_at)], ["Jev", info.kind === "fallback" ? "no answer" : `${secs(task.elapsed_seconds)} · ${pct(task.confidence)}`],
  ];
  $("readoutFacts").innerHTML = facts.map(([k, v]) => `<div><dt>${k}</dt><dd>${escapeHtml(v)}</dd></div>`).join("");
  $("readoutPrompt").textContent = run.user_prompt || "";
  const probs = Object.entries(task.probabilities || {});
  const order = state.efforts.filter(e => probs.some(([k]) => k === e)).concat(probs.map(([k]) => k).filter(k => !state.efforts.includes(k)));
  let x = 0;
  $("ratio").replaceChildren(...order.map(name => {
    const w = ratio(task.probabilities[name]) * 300;
    const r = el("rect", { x, y: 0, width: Math.max(0, w - 1), height: 10, class: name === task.effort ? "is-choice" : "" });
    x += w;
    return r;
  }));
  $("ratioLegend").textContent = order.length ? order.map(name => `${name} ${pct(task.probabilities[name])}`).join("   ") : "";
}

/* ── shift log rows ─────────────────────────────────── */
function tiles(info) {
  if (info.kind === "delegated") return `<div class="tiles"><span class="tile is-on">${escapeHtml(info.gear || "—")}</span></div>`;
  return `<div class="tiles" aria-label="${escapeHtml(info.kind === "fallback" ? `neutral, host default ${info.gear}` : `gear ${info.gear}`)}">${state.efforts.map(e =>
    `<span class="tile${e === info.gear && info.kind !== "fallback" ? " is-on" : ""}${e === info.gear && info.kind === "fallback" ? " is-default" : ""}" aria-hidden="true">${short(e)}</span>`).join("")}</div>`;
}

function barsHtml(probabilities, chosen) {
  const entries = Object.entries(probabilities || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return "";
  // SVG geometry attributes work under style-src 'self'; inline CSS widths do not.
  return `<div class="bars">${entries.map(([name, value]) => `<div class="bar-row"><span>${escapeHtml(name)}</span><svg class="bar-track" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true"><rect class="track" width="100" height="8"/><rect class="fill${name === chosen ? " is-choice" : ""}" width="${ratio(value) * 100}" height="8"/></svg><em>${pct(value)}</em></div>`).join("")}</div>`;
}

function evidenceFlowHtml(run) {
  const info = shift(run);
  const task = info.task;
  if (info.kind === "delegated") return `<div class="legacy">${lineageHtml(run)}</div>`;
  const proxy = info.proxy || {};
  const facts = [
    ["Run", run.run_id], ["Host", run.origin_provider || "—"], ["Session", sessionOf(run) || "—"],
    ["Chosen by", task.effort_source === "jev" ? "Jev" : "your default gear"],
    ["Jev latency", info.kind === "fallback" ? "—" : secs(task.elapsed_seconds)],
    ["Confidence", info.kind === "fallback" ? "—" : pct(task.confidence)],
    ["Proxied requests", fmt(proxy.requests)], ["Shifted by proxy", fmt(proxy.changed)],
    ["Model", "inherited, never routed"],
  ];
  const why = { engaged: "The proxy put this gear on every request of the turn it saw.",
    selected: "Jev chose this gear, but no request of this turn passed the effort proxy, so the host ran its own setting. Point ANTHROPIC_BASE_URL (Claude) or the jev model provider (Codex) at the proxy to engage it.",
    fallback: `Jev did not answer in time (${task.fallback_reason || "reason not recorded"}). The proxy left the requests alone and the host's own effort ran.` }[info.kind];
  return `<div><h3>Prompt</h3><pre>${escapeHtml(run.user_prompt || "Not recorded.")}</pre><p class="note">${escapeHtml(why)}</p></div>
    <div><h3>Evidence</h3><dl class="facts">${facts.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>${barsHtml(task.probabilities, task.effort)}</div>`;
}

function rowSummary(run) {
  const info = shift(run);
  const task = info.task;
  const t = clock(run.started_at);
  const host = isTurn(run) ? run.origin_provider : task.execution_provider || task.provider || run.origin_provider;
  const jev = info.kind === "fallback" || info.kind === "delegated" ? "—" : `${secs(task.elapsed_seconds)}<br>${pct(task.confidence)}`;
  const prompt = run.user_prompt || task.title || run.run_id;
  return `<span class="t-time"><b>${t.time}</b>${t.day}</span>${tiles(info)}
    <span class="state" data-state="${info.kind}"><span>${escapeHtml(info.label)}<br><small>${escapeHtml(info.note)}</small></span></span>
    <span class="t-host">${escapeHtml(host || "—")}<code>${escapeHtml(sessionOf(run) || run.run_id.slice(-12))}</code></span>
    <span class="t-jev">${jev}</span><span class="t-prompt" title="${escapeHtml(prompt.slice(0, 400))}">${escapeHtml(prompt)}</span>`;
}

function createRow(run) {
  const row = document.createElement("details");
  row.className = "shift";
  row.dataset.runId = run.run_id;
  row.innerHTML = `<summary></summary><div class="shift-body"></div>`;
  updateRow(row, run);
  return row;
}

function updateRow(row, run) {
  const info = shift(run);
  row.dataset.kind = info.kind;
  row.dataset.provider = (isTurn(run) ? run.origin_provider : info.task.execution_provider || run.origin_provider) || "";
  row.dataset.signature = `${run.source_mtime}|${JSON.stringify(run.proxy || {})}`;
  row.dataset.search = [run.user_prompt, run.run_id, run.origin_provider, info.gear, info.label, info.note].join(" ").toLowerCase();
  row.querySelector("summary").innerHTML = rowSummary(run);
  row.querySelector(".shift-body").innerHTML = evidenceFlowHtml(run);
}

function withStableViewport(mutate) {
  const host = $("runs");
  const preserve = host.getBoundingClientRect().top < 0;
  const anchor = [...host.children].find(row => row.getBoundingClientRect().bottom > 0);
  const top = anchor?.getBoundingClientRect().top;
  mutate();
  if (preserve && anchor?.isConnected && Number.isFinite(top)) window.scrollBy(0, anchor.getBoundingClientRect().top - top);
}

function applyFilters() {
  const query = state.query.toLowerCase();
  let visible = 0;
  state.rows.forEach(row => {
    const show = (!query || row.dataset.search.includes(query)) && (!state.provider || row.dataset.provider === state.provider)
      && (!state.status || row.dataset.kind === state.status);
    row.hidden = !show;
    if (show) visible += 1;
  });
  let empty = $("emptyState");
  if (!visible) {
    if (!empty) {
      empty = Object.assign(document.createElement("p"), { id: "emptyState", className: "empty" });
      $("runs").appendChild(empty);
    }
    empty.textContent = state.runs.length ? "No turns match these filters." : "No turns recorded yet. Send a prompt in Claude or Codex with the hook installed.";
  } else empty?.remove();
}

function reconcile(runs) {
  const host = $("runs");
  const previousIds = new Set(state.runs.map(run => run.run_id));
  const nextIds = new Set(runs.map(run => run.run_id));
  let changed = 0;
  withStableViewport(() => {
    state.rows.forEach((row, id) => { if (!nextIds.has(id)) { row.remove(); state.rows.delete(id); } });
    runs.forEach((run, index) => {
      let row = state.rows.get(run.run_id);
      if (!row) { row = createRow(run); state.rows.set(run.run_id, row); changed += 1; }
      else if (row.dataset.signature !== `${run.source_mtime}|${JSON.stringify(run.proxy || {})}`) { updateRow(row, run); changed += 1; }
      if (host.children[index] !== row) host.insertBefore(row, host.children[index] || null);
    });
  });
  state.runs = runs;
  updateProviders();
  updateTally();
  drawPlate();
  applyFilters();
  const added = runs.filter(run => !previousIds.has(run.run_id)).length;
  if (!state.firstLoad && (added || changed)) $("announce").textContent = `${added ? `${added} new turn${added === 1 ? "" : "s"}. ` : ""}${changed} updated.`;
  state.firstLoad = false;
}

function updateProviders() {
  const select = $("provider");
  const values = [...new Set([...state.rows.values()].map(row => row.dataset.provider).filter(Boolean))].sort();
  if (select.dataset.signature === values.join("|")) return;
  select.replaceChildren(new Option("All providers", ""), ...values.map(v => new Option(v, v)));
  select.value = values.includes(state.provider) ? state.provider : "";
  select.dataset.signature = values.join("|");
}

function updateTally() {
  const turns = state.runs.filter(isTurn).map(shift);
  const count = (k) => turns.filter(s => s.kind === k).length;
  $("tally").innerHTML = turns.length
    ? `${fmt(turns.length)} turns · <b>${fmt(count("engaged"))} engaged</b> · ${fmt(count("selected"))} selected, not engaged · ${fmt(count("fallback"))} fallback`
    : "No turns yet.";
  const gears = state.efforts.map(e => turns.filter(s => s.kind !== "fallback" && s.gear === e).length);
  const total = gears.reduce((a, b) => a + b, 0);
  let x = 0;
  $("spread").replaceChildren(...(total ? gears.map((g, i) => {
    const w = (g / total) * 300;
    const r = el("rect", { x, y: 0, width: Math.max(0, w - (w ? 1 : 0)), height: 8, class: `g${i}` });
    x += w;
    return r;
  }) : []));
  $("spread").setAttribute("aria-label", total ? state.efforts.map((e, i) => `${e} ${Math.round((gears[i] / total) * 100)}%`).join(", ") : "");
}

/* ── data ───────────────────────────────────────────── */
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
  const up = health.proxy?.listening;
  $("linkage").dataset.state = up ? "up" : "down";
  $("linkageText").textContent = up ? `Effort proxy :${health.proxy.port} engaged` : `Effort proxy :${health.proxy?.port ?? 8791} down — gears are selected, not engaged`;
}

/* ── default gears (native fallbacks) ───────────────── */
function nativePayload() {
  return { native_fallbacks: Object.fromEntries(nativeProviders.map(p => [p, { effort: $(`nativeEffort-${p}`).value || null }])) };
}

function setTone(id, text, tone) { $(id).textContent = text; $(id).dataset.tone = tone; }

function updateNativeState() {
  nativeProviders.forEach(p => { $(`nativeEffort-${p}`).disabled = nativeState.busy || !nativeState.current; });
  const changed = nativeState.current && JSON.stringify(nativePayload()) !== JSON.stringify(nativeState.current);
  $("saveNativeSettings").disabled = nativeState.busy || !changed;
  $("reloadNativeSettings").disabled = nativeState.busy;
  if (!nativeState.busy && nativeState.current) setTone("nativeSettingsState", changed ? "Unsaved" : "Saved", changed ? "pending" : "ready");
}

function renderNativeConfig(config) {
  const options = config.effort_options || [];
  if (options.length) state.efforts = options;
  nativeProviders.forEach(p => {
    const selected = config.native_fallbacks?.[p]?.effort || "";
    const select = $(`nativeEffort-${p}`);
    select.replaceChildren(new Option("Host's own setting", ""), ...options.map(e => new Option(e, e)));
    if (selected && !options.includes(selected)) select.add(new Option(`${selected} (not in efforts)`, selected));
    select.value = selected;
  });
  nativeState.current = nativePayload();
  nativeState.busy = false;
  updateNativeState();
  if (state.runs.length) { state.rows.forEach((row, id) => updateRow(row, state.runs.find(r => r.run_id === id))); updateTally(); drawPlate(); }
}

async function loadNativeConfig() {
  if (nativeState.current && JSON.stringify(nativePayload()) !== JSON.stringify(nativeState.current)) {
    setTone("nativeSettingsMessage", "Save your changed default gears before reloading.", "pending");
    return;
  }
  nativeState.busy = true;
  updateNativeState();
  try {
    renderNativeConfig(await fetchJson("/api/config"));
  } catch (error) {
    nativeState.busy = false;
    updateNativeState();
    setTone("nativeSettingsState", "Unavailable", "error");
    setTone("nativeSettingsMessage", `${error.message}. Reload levels to try again.`, "error");
  }
}

async function saveNativeConfig(event) {
  event.preventDefault();
  nativeState.busy = true;
  updateNativeState();
  setTone("nativeSettingsState", "Saving…", "pending");
  try {
    renderNativeConfig(await fetchJson("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(nativePayload()) }));
    setTone("nativeSettingsMessage", "Default gears saved. They apply whenever Jev misses the 1s cap.", "ready");
  } catch (error) {
    nativeState.busy = false;
    updateNativeState();
    setTone("nativeSettingsState", "Save failed", "error");
    setTone("nativeSettingsMessage", `${error.message}. Your choices are kept; save again.`, "error");
  }
}

/* ── polling ────────────────────────────────────────── */
function schedule() { clearTimeout(poll.timer); poll.timer = setTimeout(() => tick(false), poll.delay); }

function tick(manual = false) {
  if (poll.inflight) return poll.inflight;
  if (document.hidden && !manual) { schedule(); return Promise.resolve(); }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  $("refresh").disabled = true;
  poll.inflight = Promise.all([loadRuns(controller.signal), loadHealth(controller.signal)])
    .then(() => { poll.delay = poll.min; $("error").hidden = true; })
    .catch(error => {
      poll.delay = Math.min(poll.max, poll.delay * 2);
      $("error").textContent = error.name === "AbortError" ? "Live update timed out. Retrying." : `Live update failed: ${error.message}. Retrying.`;
      $("error").hidden = false;
    })
    .finally(() => { clearTimeout(timeout); poll.inflight = null; $("refresh").disabled = false; schedule(); });
  return poll.inflight;
}

$("search").addEventListener("input", e => { state.query = e.target.value; applyFilters(); });
$("provider").addEventListener("change", e => { state.provider = e.target.value; applyFilters(); });
$("status").addEventListener("change", e => { state.status = e.target.value; applyFilters(); });
$("refresh").addEventListener("click", () => tick(true));
$("nativeSettingsForm").addEventListener("change", updateNativeState);
$("nativeSettingsForm").addEventListener("submit", saveNativeConfig);
$("reloadNativeSettings").addEventListener("click", loadNativeConfig);
document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(true); });
drawPlate();
loadNativeConfig().finally(() => tick(true));
