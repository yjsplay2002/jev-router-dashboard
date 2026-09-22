---
name: jev-router
description: Delegate substantial tasks across Claude, Codex, and Grok CLI workers when explicit routing, meaningful parallel work, specialist model selection, or independent review is needed. Handle ordinary questions and clear small edits directly. Never recurse inside child workers.
---

# Jev task routing

## Prompt provenance

When routing, include the user's original instruction verbatim in the manifest's top-level `user_prompt` field (omit secrets). Preserve task-specific rewrites separately as `tasks[].prompt`; never substitute them for the original. Include `origin_provider`, and an optional `parent_agent` identifier only if known. The runner records dependency-expanded prompts and adapters record the actual CLI-submitted prompt, its hash, session IDs, and reported models. Never invent an observed model from the requested model. Legacy runs may lack these fields; label them unrecorded rather than reconstructing history. This evidence requirement does not change selective routing criteria.

## Selective routing

Decide locally whether delegation adds meaningful value before invoking Jev. Do not call the classifier just to decide whether to route.

Use Jev when the user explicitly requests routing/model comparison, or when substantial work benefits from independently scoped parallel tasks, specialist model selection, or an independent review. State the concrete benefit before routing.

Handle ordinary questions, explanations, status checks, follow-ups, local lookups, opening tools, and clear small edits directly. Length alone or the presence of multiple steps does not justify delegation. When uncertain and no concrete delegation benefit is apparent, work directly.

Never route inside a bounded child worker or when JEV_ROUTER_CHILD=1. Respect cancellation and opt-out immediately; do not duplicate an ongoing task. This preference does not expand permissions. When routing is used, run the normal workflow below and report actual run.json evidence, including failures and parent verification. Direct work needs no fabricated routing record or per-model usage report.


Use the globally installed `jev-router` command. On Windows with a stale `PATH`, use `& "$env:USERPROFILE/.local/bin/jev-router.exe"`.

## Route work

1. Create a UTF-8 task manifest with `origin_provider` and a `tasks` array. Every task needs a unique `id`, concise `title`, self-contained `prompt`, and optional `depends_on` IDs. Include only relevant context, paths, constraints, acceptance criteria, and the bounded deliverable.
2. Run `jev-router run --tasks <manifest.json> --cwd <workspace>`. Use `--allow-edits` only when the user authorized edits. Do not run the router when `JEV_ROUTER_CHILD=1` or when acting as an explicitly bounded child worker.
3. Read the returned `run_file` and actual task results. Verify material claims independently before integrating them. CLI exit success alone is not quality verification.
4. In the final response, report selected CLI/provider, observed model (or requested/unknown), requested reasoning effort, actual task outcome, measured usage when available, and parent integration or verification separately. Never infer savings without a valid baseline run.

Use `jev-router plan` only when the user asks for a plan without execution. Use `jev-router benchmark` only for an explicit impact or baseline comparison request. Do not silently substitute `shadow` or planning for a normal routed run.

## Run records

Jev writes each routing decision to `~/.config/jev-router/runs/<run-id>/run.json`. Treat this file as the source of truth for:

- difficulty and category classification;
- routing probabilities, confidence, selected provider, reasoning effort, and fallback reason;
- requested and observed models;
- status, duration, exit code, token usage, result, and report location.

These records can contain prompts, results, and local paths. Do not publish or commit the run directory. When reporting publicly, summarize or redact sensitive content.

## Local dashboard

To inspect routing history visually, run:

```text
python scripts/jev_dashboard.py --open
```

The bundled dashboard is dependency-free and binds to `127.0.0.1:8787`. It reads the standard Jev runs directory, tolerates older or malformed records, shows sanitized task prompts and task-to-model evidence flows, and can update only Jev's fallback provider/model/effort fields through its always-open policy editor. Run records stay read-only. Use `--runs-dir <path>` or `--config <path>` for custom locations. Use `--include-content` only when full sanitized result text is genuinely needed locally.

Never expose the dashboard on a non-loopback interface. The script deliberately refuses such binds.
