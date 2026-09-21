---
name: jev-router
description: Route tasks across authenticated Claude, Codex, and Grok CLI workers with Jev classification and provider evaluation; preserve auditable run records and optionally inspect them in a localhost-only dashboard. Do not invoke recursively inside a routed child worker.
---

# Jev task routing

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

The bundled dashboard is read-only, dependency-free, and binds to `127.0.0.1:8787`. It reads the standard Jev runs directory, tolerates older or malformed records, and hides full prompts by default. Use `--runs-dir <path>` for a custom location. Use `--include-content` only when full sanitized prompt and result text is genuinely needed locally.

Never expose the dashboard on a non-loopback interface. The script deliberately refuses such binds.
