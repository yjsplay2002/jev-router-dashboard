# Jev Router Dashboard

A local dashboard for understanding how Jev chose a task's reasoning effort, what the routed native subagent actually did, and which fallback effort the next task will use. The model is never routed: switching models throws away the host's prompt cache, so the parent's model is inherited unchanged and only the effort moves.

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Runtime](https://img.shields.io/badge/runtime-Python%203.10%2B-66f2c2)

## What it shows

- difficulty and category classification;
- effort selection confidence and probability distribution, and whether Jev or your own setting chose it;
- an always-open `prompt → task/dependencies → effort decision` evidence diagram for every run;
- fallback decisions and reasons;
- routed effort and the inherited model;
- status, duration, exit code, token usage, and a redacted result summary;
- links to Jev's generated local HTML reports.
- always-expanded task details with automatic live updates as new run records appear.
- independent native fallback effort selectors for Codex, Claude, and Grok;

Probability records are normalized whether Jev stored them as fractions (`0.73`) or percentages (`73`), so both labels and gauges render as `73%`. The dashboard reads existing `~/.config/jev-router/runs/*/run.json` files and never modifies run data. Its only write operation atomically updates native provider defaults in Jev's local `config.json`; unrelated settings are preserved. It does not call external services or require a database.

## Native provider defaults

The first settings panel lets you choose a fallback effort independently for each provider. It applies whenever Jev does not answer within the 1 second cap. Saving Codex does not replace Claude or Grok's settings. Choose **No default — parent handles fallback** to clear a provider's preference. The levels come from Jev's own `efforts` list; the dashboard reads no model catalog and launches no CLI.

```json
{"native_fallbacks":{"codex":{"effort":"medium"},"claude":{"effort":null},"grok":{"effort":null}}}
```

The router skill (`SKILL.md`) reads only the actual parent's provider entry. Codex stays within OpenAI/Codex, Claude within Claude, and Grok within Grok. A host that exposes no effort parameter records that effort control was unavailable rather than inventing a value; an unset level leaves the fallback choice with the parent.

`GET /api/config` returns all three native entries plus the available `effort_options`; `PUT /api/config` accepts a partial `native_fallbacks` object and preserves omitted providers. A null or empty effort clears that entry. Unknown providers, unknown levels and any attempt to set a model are rejected without writing. Obsolete CLI policy fields are neither exposed nor editable; existing unrelated configuration is preserved.

## Run locally

Python 3.10 or newer is sufficient; there are no third-party runtime dependencies.

```bash
git clone https://github.com/yjsplay2002/jev-router-dashboard.git
cd jev-router-dashboard
python scripts/jev_dashboard.py --open
```

Then visit <http://127.0.0.1:8787>.

Useful options:

```text
--runs-dir PATH       Read another Jev run directory
--config PATH          Read and update another Jev config.json
--port PORT           Use another localhost port
--max-runs N          Limit history (default: 500)
--include-content     Include full sanitized results in the API
--open                Open the default browser
```

## Install as a Codex skill

Copy the repository contents into the `jev-router` skill directory, or copy `SKILL.md`, `scripts/`, and `dashboard/` into an existing installation. The skill instructs the parent agent to ask `scripts/jev_effort.py` for an effort, echo its one-line decision to the user, dispatch the native subagent with that effort and no model override, then verify the result and disclose the inherited model and routed effort in its final response.

On Windows, a typical destination is `%USERPROFILE%\.codex\skills\jev-router`.

## Privacy and security

- Binds only to loopback and refuses public/network binds.
- Run history and reports remain read-only. Only `PUT /api/config` is writable; the only accepted field is `native_fallbacks`.
- Config writes require same-origin JSON requests, validate the effort against Jev's configured levels, and use atomic file replacement.
- Displays sanitized recorded task prompts in the local evidence diagram; common secret patterns and home-directory paths are redacted.
- Truncates and scrubs common API keys, bearer tokens, passwords, secrets, GitHub tokens, and home-directory paths from displayed summaries.
- Serves no CDN assets, analytics, fonts, or telemetry.
- Validates run IDs and report paths to prevent directory traversal.
- Sends restrictive CSP, frame, MIME-sniffing, referrer, and cache headers.

Run records and prompts can still contain sensitive data on disk. Never commit `~/.config/jev-router/runs` or publish screenshots without reviewing them.

## Test

```bash
python -m unittest discover -s tests -v
```

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
# Effort routing

Ask once per bounded task, then echo the line it prints:

```bash
python scripts/jev_effort.py "add a retry guard to the order submit path" --provider codex --json
jev: effort=high (jev-1.13.0, 0.42s, conf 81%) - model unchanged
```

The cap is 1.0 second of wall clock, measured with a monotonic clock around a worker thread, so a slow DNS lookup or a hung socket cannot stall the task. Past the cap the decision is abandoned and your configured effort applies; `--json` then reports `route.fallback` with the reason instead of probabilities. The only text sent to TypeSafe is the bounded task description, truncated to `judge_context_chars`; file contents and credentials are never sent. The script exits 0 in every case and prints no model, because the model is inherited.

The always-open lineage view and SVG dependency graph distinguish Jev recommendation, fallback/override, actual execution provider, requested model, observed model IDs, session IDs, and PID. A requested alias is never presented as an observed model. Missing historical originals/telemetry remain unrecorded; a legacy prompt.txt is read only when confined to its run directory. Prompt text is redacted for display, with explicit truncation notices for expanded/submitted text over 100,000 characters. SHA-256 describes the original stored submitted text, before display redaction.
