# Jev Router

[한국어](README.ko.md)

A skill for Claude Code, Codex and Grok that picks the reasoning effort for each turn with Jev, plus a local dashboard for the records it leaves. **The model is never routed.** Switching models discards the host's prompt cache, which costs more than a cheaper model saves, so the model you selected is kept and only the effort changes.

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Runtime](https://img.shields.io/badge/runtime-Python%203.10%2B-66f2c2)

## What's in the repository

| Path | Role |
| --- | --- |
| `scripts/jev_effort_hook.py` | `UserPromptSubmit` hook. Asks Jev for this turn's effort before the turn starts and tells the model how deeply to work. |
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

**By default the hook does not change the host's effort setting.** No host accepts an effort level from a hook. The behavioural instruction works on every host; the numeric level only changes if you run the printed command yourself: Claude and Grok `/effort <level>`, Codex `Alt+.` / `Alt+,` or `/model`. Codex has no skill-scoped effort override (openai/codex#22908) and Claude's `effort:` frontmatter is reported as inert (anthropics/claude-code#69267).

### Auto-apply (opt-in)

Set `"auto_apply_effort": true` in `~/.config/jev-router/config.json` and, when Jev answers, the hook writes the routed level into the host's own settings instead of printing a command:

| Host | Written to | Takes effect |
| --- | --- | --- |
| Claude | `effortLevel` in `settings.json` (`$CLAUDE_CONFIG_DIR` respected) | When Claude Code rereads settings; not the turn already running |
| Codex | top-level `model_reasoning_effort` in `config.toml` (`$CODEX_HOME` respected) | Next Codex session |
| Grok | nothing (no file setting); the command is still printed | — |

Caveats: the effort lags at least one turn; the setting is global, so other open sessions pick it up too; a per-model `modelSettings.<model>.effortLevel` in Claude overrides the top-level value; and a fallback (no API key, timeout) leaves the file untouched. The write is atomic and skipped when the value is already set.

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

1. Copy the repository into the skill directory, e.g. `~/.claude/skills/jev-router` (Claude) and/or `~/.codex/skills/jev-router` (Codex). Grok's hook can point at either copy.
2. Put the API key in `~/.config/jev-router/.env` as `TYPESAFE_API_KEY=...` (or `$JEV_ROUTER_HOME/.env`).
3. Add a `UserPromptSubmit` hook. The last argument is the host (`claude`, `codex` or `grok`):

   | Host | File |
   | --- | --- |
   | Claude | `~/.claude/settings.json` |
   | Codex | `~/.codex/hooks.json` |
   | Grok | `~/.grok/hooks/jev-effort.json` |

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

Configuration lives in `~/.config/jev-router/config.json` (`$JEV_ROUTER_HOME` overrides the directory). The keys used are `efforts`, `judge_context_chars`, `native_fallbacks` and `auto_apply_effort`.

## Dashboard

```bash
python scripts/jev_dashboard.py --open   # http://127.0.0.1:8787
```

It shows, for each run:

- difficulty and category, the chosen effort, its confidence and probability distribution, and whether Jev or your setting chose it;
- a `prompt → task/dependencies → effort decision` evidence diagram;
- fallback decisions and reasons, the inherited model, status, duration, token usage and a redacted result summary;
- live updates as new records appear.

In a hook record, the effort is the routed (or fallback) level. It does not show whether you actually applied it with `/effort`.

### Per-provider fallback effort

The first settings panel sets the effort each provider uses when Jev does not answer in time. Each provider is saved separately; **No default — parent handles fallback** clears one. Levels come from the `efforts` list.

```json
{"native_fallbacks":{"codex":{"effort":"medium"},"claude":{"effort":null},"grok":{"effort":null}}}
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
- No CDN assets, analytics, fonts or telemetry. Validates run IDs and report paths against directory traversal. Sends restrictive CSP, frame, MIME-sniffing, referrer and cache headers.

Run records contain full prompts on disk (up to 12,000 characters). Never commit `~/.config/jev-router/runs` or publish screenshots without reviewing them.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
