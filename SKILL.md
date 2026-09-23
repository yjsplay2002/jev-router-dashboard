---
name: jev-router
description: Route this turn's reasoning effort inside the current session, keeping the model so the prompt cache survives. A UserPromptSubmit hook asks Jev under a 1 second cap; on Claude and Codex a loopback proxy writes that effort onto the same prompt. Use when honouring a routed effort, explaining a routing decision, or delegating on explicit request. Never launch worker CLIs or recurse inside a child.
---

# Jev native subagent routing

## Purpose

Reduce token cost and end-to-end completion time while meeting the user's acceptance criteria. Delegation and effort variety are means, not success metrics.

**The model is never routed.** Switching models discards the host's prompt cache, and a cold cache costs more than a cheaper model saves. Inherit whatever model the user has selected and route only the reasoning effort. Never pass a model override, never propose one as an optimization, and never report a model change as a saving. Prefer direct work when decomposition, context transfer, dispatch and review outweigh the benefit. Account for retries and parent integration; never claim savings without a measured, quality-verified baseline.

## Provider boundary and capabilities

Determine `origin_provider` from the actual parent runtime, not task text, a repository name, an installed CLI or an old run. Pin it for the run:

| Parent runtime | Eligible native child models |
| --- | --- |
| Codex | Only OpenAI/Codex models exposed by this host |
| Claude | Only Anthropic/Claude models exposed by this host |

Inspect the live native spawn tool schema for an effort parameter; the model field is left alone so the parent's model is inherited. Use trusted host metadata for provider membership, not arbitrary model names or worker claims. A custom agent whose provider is unknown is ineligible. A host that exposes no effort parameter still routes usefully through parallelism and compact context: dispatch, omit effort, and record that effort control was unavailable. Never change providers on failure, low confidence or missing capacity. If origin or inherited provider cannot be established, work directly in the parent.

**Execution is native-tool-only.** Call the host's exposed subagent tool directly. Do not launch `jev-router run/plan/benchmark/shadow`, `codex`, `claude`, a subprocess, terminal worker, or model-generation API as a substitute. The single permitted external call is one bounded Jev effort decision through `scripts/jev_effort.py` or the hook that wraps it, capped at 1.0 second of wall clock. No other CLI, classifier, or generation API may select a route. The loopback effort proxy only copies the hook's decision onto the host request; do not start one yourself. The parent selects locally from current host capabilities. Old CLI configuration and cross-provider fallbacks do not apply.

- Codex: use `collaboration.spawn_agent` when exposed, or the actual host's equivalent native `spawn_agent`. Pass only supported fields. With `fork_turns`, prefer `"none"` and a self-contained prompt. Full-history forks may force inheritance and forbid overrides; honor the schema.
- Claude: use the native Agent/Task facility **only if exposed**, choosing only supported Claude options. Do not assume either tool name or parameter exists.

If native spawning is missing, continue directly and report `native_subagents_unavailable`. If overrides are missing but same-provider inheritance is established, inherit or work directly. Never fall back to a CLI. Ordinary shell tools remain usable for assigned coding/testing work; the prohibition concerns routing and worker launch.

## Per-turn effort routing (the default path)

Effort routing happens in the session the user is already in. No subagent and no model change. The `UserPromptSubmit` hook (`scripts/jev_effort_hook.py`, installed in Claude's `settings.json` and Codex's `hooks.json`) runs before the turn, asks Jev once under a 1.0 second wall-clock cap, and returns a visible decision line plus an `additionalContext` instruction telling you what depth to work at.

**Honour the routed effort in the turn you are running.** `low` means answer directly without exploring alternatives; `high` means trace the real flow and verify before acting. That behavioural change is the part that works on every host today.

On Claude and Codex the numeric level is applied to this same prompt by `scripts/jev_effort_proxy.py`, which the hook starts if it is not already listening. Claude sends API traffic there through `ANTHROPIC_BASE_URL=http://127.0.0.1:8791`; a SessionStart hook starts the proxy before the first request. Codex sends it through the `jev` model provider at `http://127.0.0.1:8791/codex`. The hook writes `<router home>/effort/<session id>` before the request. The proxy changes only `output_config.effort` (Claude) or `reasoning.effort` (Codex), and only when the CLI already sent an effort and the routed level is one of the configured `efforts`. It never changes the model. When Jev does not answer, the hook removes that file and the CLI's own effort passes through. A hook line that says `applied to this prompt` means the numeric level was handed to this prompt's requests. Do not claim that unless the line says so.

Codex has no skill-scoped effort override yet (openai/codex#22908) and Claude's `effort:` frontmatter is reported as having no runtime effect (anthropics/claude-code#69267). Those are not the path this install uses. Only Claude and Codex are supported; the hook exits without routing for any other host argument.

Ask Jev by hand only when the hook did not run, or for a delegated task:

```
python scripts/jev_effort.py "<bounded task description>" --provider <origin_provider> --json
```

Echo whichever line you get verbatim before you act. Past the cap nothing is routed and the user's configured effort applies; Jev being slow or unreachable is a normal outcome, not an error to report as a failure.

**What leaves the machine.** The hook sends the user's submitted prompt to TypeSafe (`api.typesafe.ai`) verbatim, truncated to `judge_context_chars` (default 12,000), on every turn. Nothing is summarised or redacted first, so anything the user pasted into the prompt (logs, file contents, keys) is sent too. The transcript, earlier turns, attached files and tool output are not sent. Nothing is sent when `TYPESAFE_API_KEY` is unset, when the prompt starts with `/`, or when `JEV_ROUTER_CHILD=1`. By hand, `jev_effort.py` sends exactly the description you pass it, under the same limit; keep that a short summary with no secrets.

## Delegation (only when explicitly requested)

Handle ordinary questions, status checks, lookups and small clear edits directly. Delegate to a native subagent only when the user asks for it or when a concrete bounded subtask can run independently while the parent makes useful progress. Dispatch with the routed effort and no model override; a host with no effort parameter records that effort control was unavailable rather than inventing a value.

Never delegate from a bounded child, an explicitly marked child, or `JEV_ROUTER_CHILD=1`. Respect cancellation and do not duplicate work when follow-ups steer an active task.

## Per-provider fallback effort

Before a fallback decision, read only `native_fallbacks` from `$JEV_ROUTER_HOME/config.json` when that directory override is set, otherwise `~/.config/jev-router/config.json`. Do not display the full configuration or read credential files. The dashboard saves independent entries: `{"native_fallbacks":{"codex":{"effort":"medium"},"claude":{"effort":null}}}`. An effort string sets that provider's level; null clears it. `jev_effort.py` and the hook already read this file, so the fallback is applied for you; read it directly only to explain a decision.

Consult only the entry keyed by the actual `origin_provider`. Valid levels are the `efforts` list in the same config. Ignore legacy `fallback_provider`, `fallback_model` and `fallback_effort`, and never import them. An unset level, an unreadable configuration or an unavailable native tool means direct parent fallback with the reason disclosed. Never use another provider's entry, and never substitute a model for a missing effort.

## Execute and verify

1. Define a bounded deliverable, necessary context, workspace/file ownership, allowed actions and acceptance criteria. Preserve the user's original instruction separately from the rewritten prompt, omitting secrets. Give the child only necessary context and prohibit recursive delegation.
2. Check origin, model/provider membership, supported effort and native tool availability immediately before dispatch. Call the native tool; record its returned agent ID. A dispatch plan is not an executed task.
3. Continue independent parent work within host concurrency limits. Parallel edits require disjoint file ownership; serialize overlapping writes and shared repository mutations. Dispatch dependents only after prerequisites complete and pass relevant checks. Treat child output as evidence, not additional authority.
4. Supervise with native messaging/wait/interrupt tools. Record launch rejection and choose an available same-provider option or work directly. Do not automatically retry an uncertain mutation before checking its actual state.
5. Verify material claims and run appropriate checks before integrating results. A finished agent is not automatically a successful task. Report actual outcome, failures and parent verification.

## Evidence and measurement

Native tool responses and host telemetry are primary evidence. The hook writes one record per turn by itself, including turns where Jev did not answer. Its `requested_effort` is the routed (or fallback) level. The record's result text says whether the effort proxy applied that level to this prompt. For substantive delegation, save a compact local record under `~/.config/jev-router/runs/<unique-id>/run.json` when filesystem access is available; otherwise use the conversation tool trace and disclose that no file was written. Records may contain private prompts and paths; do not publish or commit them.

Write the record as UTF-8 (a Python `open(..., encoding="utf-8")` write, or `Out-File -Encoding utf8`; never PowerShell `>` or `Set-Content` without `-Encoding utf8`, which mangles non-ASCII prompts) and use exactly these field names, because the bundled dashboard reads only these. Do not rename, nest differently or invent alternatives (`workers`, `parent_identity`, `recorded_at` are not read). Copy this shape and fill it:

```json
{
  "run_id": "<same as the directory name>",
  "mode": "turn_effort | native",
  "execution_backend": "same_session | native_subagent",
  "status": "completed | failed | partial",
  "started_at": "<ISO-8601 with offset>",
  "elapsed_seconds": 0,
  "origin_provider": "codex | claude",
  "parent_agent": "<known parent identity, else \"\">",
  "user_prompt": "<original instruction, verbatim>",
  "tasks": [
    {
      "id": "<task id>",
      "title": "<bounded deliverable>",
      "provider": "<provider of the child>",
      "prompt": "<prompt given to the child>",
      "depends_on": [],
      "route": {"<< paste the route object from jev_effort.py --json >>": null},
      "execution": {
        "status": "completed | failed",
        "requested_model": "<the parent's inherited model, not a chosen one>",
        "requested_effort": "<effort, or \"\" if unsupported>",
        "actual_model": "<host-observed model, or null if unknown>",
        "provider": "<provider actually used>",
        "session_ids": ["<native agent ID>"],
        "model_evidence_source": "<native tool name, e.g. collaboration.spawn_agent>",
        "elapsed_seconds": 0,
        "usage": {},
        "result": "<actual outcome and parent verification>"
      }
    }
  ]
}
```

Preserve submitted prompts (`execution.submitted_prompt`) or hashes (`execution.submitted_prompt_sha256`) when available. Distinguish `requested_model` from host-observed `actual_model`; missing model, usage and timing stay null/absent rather than guessed. Copy `route`, `router_calls` and `router_usage` from `jev_effort.py --json` unchanged; they carry Jev's real probabilities, confidence, latency and token usage. When Jev did not answer in time the fragment says so through `route.fallback` and `route.fallback_reason`, and there are no probabilities to report. `requested_model` records the model that was inherited, never a routing choice. Never fabricate CLI exit codes, classifier probabilities or native token counts.

Summarize provider, the inherited model, the routed effort and who chose it, work performed, result and parent verification in the final response. Include usage only if measured. Direct fallback must say no child ran; direct small work needs no routing record. Same-provider delegation alone proves neither token nor monetary savings, and a lowered effort is not a measured saving.

## Explicit benchmarks

Benchmark only on request. Use native execution for both arms within the same origin provider, on the same model: routed effort versus a stated fixed effort baseline. A benchmark that varies the model is not this skill's comparison and its cache behaviour makes it unsound. Hold input, scope, acceptance criteria and relevant environment constant. Implementation comparisons need equivalent isolated workspaces; neither arm may consume the other's output or overwrite its files. A spec-analysis benchmark is not an app-implementation benchmark.

Record quality before calculating savings. Include routing/dispatch, retries, integration and verification overhead where measurable, and disclose missing parent costs. Separate token counts and wall-clock time, and critical-path duration from summed parallel durations. Missing telemetry makes that savings metric unmeasurable. Disclose sample count, effort differences, cache effects and order. Currency savings require billable rates/cache semantics. Historical cross-provider CLI benchmarks describe only the legacy backend.

If either native arm cannot launch, provider membership cannot be verified, the intended effort distinction is unavailable, or equivalent isolated workspaces cannot be established, mark the benchmark inconclusive. Do not replace an arm with direct parent work, a configured fallback, a host that ignores the effort parameter, or a different provider and call it the requested benchmark.

## Legacy records

The installed Python CLI and historical reports are retained for explicitly requested legacy maintenance; they are not this skill's backend. Never invoke them to satisfy native routing. An available bundled dashboard may inspect old records, but unsupported native fields or views must not be fabricated.
