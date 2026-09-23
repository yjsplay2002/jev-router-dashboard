# Jev Router

[한국어](README.ko.md)

A skill for Claude Code and Codex that picks the reasoning effort for each turn with Jev, plus a local dashboard for the records it leaves. **The model is never routed.** Switching models discards the host's prompt cache, which costs more than a cheaper model saves, so the model you selected is kept and only the effort changes.

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Runtime](https://img.shields.io/badge/runtime-Python%203.10%2B-66f2c2)

## What's in the repository

| Path | Role |
| --- | --- |
| `scripts/jev_effort_hook.py` | `UserPromptSubmit` hook. Asks Jev for this turn's effort before the turn starts, tells the model how deeply to work, and hands the level to the proxy. |
| `scripts/jev_effort_proxy.py` | Loopback proxy. Writes that level onto the same prompt's API request. |
| `scripts/jev_effort.py` | The single Jev call (1.0 s cap). The hook uses it, and you can run it by hand. |
| `SKILL.md` | Instructions the agent follows: honour the routed effort, delegate only on request, record evidence. |
| `scripts/jev_dashboard.py`, `dashboard/` | Local dashboard for run records and per-provider fallback effort. |
| `tests/` | `python -m unittest discover -s tests -v` |

No third-party runtime dependencies; Python 3.10 or newer.

## How per-turn routing works

1. You submit a prompt. The host runs the hook with the prompt on stdin.
2. The hook asks Jev once, capped at 1.0 second of wall clock (monotonic clock around a worker thread, so a slow DNS lookup or hung socket cannot stall the turn).
3. It returns a visible line with the decision and an `additionalContext` instruction telling the model what depth to work at (`low` = answer directly, `high` = trace the real flow and verify).
4. It writes one run record to `~/.config/jev-router/runs/<id>/run.json`.

```bash
echo '{"prompt":"trace why the migration drops rows"}' | python scripts/jev_effort_hook.py claude
jev: effort=high (jev-1.13.0, 0.39s, conf 99%) - model unchanged -> apply: /effort high

python scripts/jev_effort.py "add a retry guard to the order submit path" --provider codex --json
jev: effort=medium (jev-1.13.0, 0.58s, conf 95%) - model unchanged
```

**Claude and Codex apply the numeric level to the same prompt.** No host accepts an effort parameter from a hook, so the hook writes `<router home>/effort/<session id>` and `jev_effort_proxy.py` sets the effort field on that session's requests before they leave the machine. The model field is not touched.

| Host | How the request is steered | What the proxy sets |
| --- | --- | --- |
| Claude | `ANTHROPIC_BASE_URL=http://127.0.0.1:8791` in `settings.json`. A SessionStart hook starts the proxy. | `output_config.effort`, only if the CLI already sent one |
| Codex | `model_provider = "jev"` with `base_url = "http://127.0.0.1:8791/codex"` | `reasoning.effort`, only if the CLI already sent one |

The proxy rewrites a request only when the routed level is in the configured `efforts` list. If Jev does not answer, the hook deletes the session file and the CLI's own effort passes through. A visible line ending in `applied to this prompt` means this prompt's requests carried the routed level. Codex has no skill-scoped effort override (openai/codex#22908) and Claude's `effort:` frontmatter is reported as inert (anthropics/claude-code#69267); neither is required for this path. Prompt cache, measured 2026-09-23: on Claude, switching the effort between turns of one session kept reading the whole prefix from cache (only the new turn was written). On Codex the first turn in an effort level the session had not used yet read nothing from cache, while returning to a level used earlier hit it, so the cache appears to be kept per effort level there. A repeat run on the same day (one session each, `codex exec` + `resume`):

| Run | Effort per turn | Cached share of input |
| --- | --- | --- |
| Control | medium ×6 | 0% (first turn), 99, 99, 99, 98, **0%** |
| Switching | high, low, low, high, medium, medium, low, high | 65% (first turn), **0**, 99, 98, **0**, 99, 99, 99 |

Both misses in the switching run fell exactly on the first use of a new level (first low, first medium); all three returns to a level already used hit. In practice a Codex session pays about one cache miss per effort level, the first time it uses it. The control run also missed once without any effort change, and the first switching turn read 65% from cache although that level had not been used in that session, so Codex's cache is not strictly partitioned by effort and also misses on its own. One session per run is a small sample.

The hook falls back to your configured effort, and never blocks the turn, when:

- `TYPESAFE_API_KEY` is not set (no request is made);
- Jev takes longer than 1.0 s (measured: 0.39–0.48 s warm, about 1.02 s on a cold TLS handshake);
- Jev is unreachable or returns no usable level.

Prompts starting with `/` and runs with `JEV_ROUTER_CHILD=1` are skipped entirely. Any error exits 0 with no output.

## What leaves your machine

- **The hook sends your submitted prompt to TypeSafe (`api.typesafe.ai`) verbatim on every turn**, truncated to `judge_context_chars` (default 12,000 characters). It is not summarised or redacted first: if you paste logs, file contents or keys into the prompt, they are sent.
- The conversation transcript, earlier turns, attached files and tool output are not sent.
- `jev_effort.py` sends exactly the description you pass it, under the same limit.
- The dashboard makes no external calls.

If that is not acceptable for a project, leave `TYPESAFE_API_KEY` unset or remove the hook entry.

## Install

1. Copy the repository into the skill directory, e.g. `~/.claude/skills/jev-router` (Claude) and/or `~/.codex/skills/jev-router` (Codex).
2. Put the API key in `~/.config/jev-router/.env` as `TYPESAFE_API_KEY=...` (or `$JEV_ROUTER_HOME/.env`).
3. Add a `UserPromptSubmit` hook. The last argument is the host (`claude` or `codex`):

   | Host | File |
   | --- | --- |
   | Claude | `~/.claude/settings.json` |
   | Codex | `~/.codex/hooks.json` |

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {"hooks": [{"type": "command", "command": "python /path/to/jev-router/scripts/jev_effort_hook.py claude", "timeout": 3}]}
       ]
     }
   }
   ```

   Add it next to any existing `UserPromptSubmit` hooks rather than replacing them.

   On Windows, Codex runs that command through the user shell, and a quoted `python.exe` path under `Program Files` does not start. Point the Codex hook at `scripts/jev-effort.cmd` with a path that contains no spaces (`jev-effort.cmd codex`, and `jev-effort.cmd codex --session-start` so the proxy is up before the first request). Changing the command makes Codex skip it until you trust the new one in `/hooks`.

Configuration lives in `~/.config/jev-router/config.json` (`$JEV_ROUTER_HOME` overrides the directory). The keys used are `efforts`, `judge_context_chars` and `native_fallbacks`. The proxy listens on `127.0.0.1:8791` (`$JEV_EFFORT_PROXY_PORT` overrides the port; the hook and the base URLs must use the same port).

## Dashboard

```bash
python scripts/jev_dashboard.py --open   # http://127.0.0.1:8787
```

The dashboard is drawn as a gearbox shift gate: every prompt is a gear change, the hook *selects* the gear and the proxy *engages* it.

- **Gate plate:** an H-pattern gate built from your `efforts` list with the knob in the gear the last prompt ran in, the engagement state, host, session, Jev latency and confidence, the prompt excerpt and Jev's probabilities.
- **Top bar lamp:** whether the effort proxy on `:8791` is listening.
- **Jev spend:** total cost of the recorded Jev calls, call count, input tokens and average per call above the log; each row and the gate plate show that turn's cost. Jev returns token counts, not cost, so the dashboard multiplies the recorded tokens by the list price (`JEV_PRICE_PER_MTOK` in `scripts/jev_dashboard.py`: $0.042 per million input tokens, output free, checked 2026-09-23). A fallback turn with no Jev call costs $0.
- **Default gear:** the per-provider fallback effort, editable in place.
- **Shift log:** one row per turn, expandable to the prompt and the evidence. Older delegated runs keep their `prompt → tasks → effort` evidence flow.

Every model request that passes the proxy is logged with its model, the effort it left with and the effort the CLI asked for (never any prompt content), so each turn shows exactly which model ran at which effort level. Requests from before this logging existed show "not observed".

Each turn is joined to the proxy's request log (`<router home>/effort/applied.log`) by session and time and labelled:

| Label | Meaning |
| --- | --- |
| Engaged | The proxy put the routed level on the turn's requests (with how many it actually changed). |
| Selected, not engaged | Jev chose a level but no request of that turn passed the proxy, so the host's own setting ran. |
| Fallback | Jev did not answer within the cap; the host's own setting ran. |

Engagement is never inferred without log evidence.

### Per-provider fallback effort

The **Default gear** panel sets the effort each provider uses when Jev does not answer (timeout or server error). Each provider is saved separately; **Host's own setting** clears one, and then a failed Jev call routes nothing: the hook adds no effort instruction and the CLI's own level runs the turn. Levels come from the `efforts` list.

```json
{"native_fallbacks":{"codex":{"effort":"medium"},"claude":{"effort":null}}}
```

`GET /api/config` returns the three entries plus `effort_options`. `PUT /api/config` accepts a partial `native_fallbacks` object and keeps omitted providers. Unknown providers, unknown levels and any attempt to set a model are rejected without writing. Other settings in the file are preserved.

Options:

```text
--runs-dir PATH       Read another run directory
--config PATH         Read and update another config.json
--port PORT           Use another localhost port
--max-runs N          Limit history (default: 500)
--include-content     Include full sanitized results in the API
--open                Open the default browser
```

### Dashboard security

- Binds only to loopback and refuses public binds.
- Run history is read-only. The only write is `PUT /api/config` (`native_fallbacks` only), which requires a same-origin JSON request and replaces the file atomically.
- Scrubs common API keys, bearer tokens, passwords, GitHub tokens and home-directory paths from displayed prompts and summaries.
- No CDN assets, remote fonts, analytics or telemetry; the one typeface (Barlow Condensed, SIL OFL) is served from `dashboard/fonts/`. Validates run IDs and report paths against directory traversal. Sends restrictive CSP, frame, MIME-sniffing, referrer and cache headers.

Run records contain full prompts on disk (up to 12,000 characters). Never commit `~/.config/jev-router/runs` or publish screenshots without reviewing them.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
