# Jev Router Dashboard

A local dashboard for understanding how Jev classified a task, why it selected a provider/model/effort level, what the routed worker actually did, and which fallback policy the next task will use.

![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Runtime](https://img.shields.io/badge/runtime-Python%203.10%2B-66f2c2)

## What it shows

- difficulty and category classification;
- provider/model selection confidence and probability distribution;
- an always-open `prompt → task/dependencies → model decision` evidence diagram for every run;
- fallback decisions and reasons;
- requested effort and observed model;
- status, duration, exit code, token usage, and a redacted result summary;
- links to Jev's generated local HTML reports.
- always-expanded task details with automatic live updates as new run records appear.
- independent native fallback model selectors for Codex, Claude, and Grok;
- a separate legacy CLI fallback editor for provider, model, reasoning effort and confidence threshold.

Probability records are normalized whether Jev stored them as fractions (`0.73`) or percentages (`73`), so both labels and gauges render as `73%`. The dashboard reads existing `~/.config/jev-router/runs/*/run.json` files and never modifies run data. Its only write operation atomically updates native provider defaults or the legacy fallback fields and confidence threshold in Jev's local `config.json`; unrelated settings are preserved. It does not call external services or require a database.

## Native provider defaults

The first settings panel lets you choose a fallback model independently for each provider. Saving Codex does not replace Claude or Grok's settings. Choose **No default — parent handles fallback** to clear a provider's preference. The model lists come from local caches; the dashboard does not launch a CLI to discover models.

```json
{"native_fallbacks":{"codex":{"model":null},"claude":{"model":null},"grok":{"model":null}}}
```

The [native router skill](https://github.com/yjsplay2002/jev-cli-router/blob/main/skill/SKILL.md) reads only the actual parent's provider entry. Codex routes only within OpenAI/Codex, Claude within Claude, and Grok within Grok. A saved preference is rechecked against the host's live native capabilities; an unavailable or unset model leaves fallback work with the parent. These defaults do not enable unsupported native tools or change the ordinary routing choice.

`GET /api/config` returns all three native entries; `PUT /api/config` accepts a partial `native_fallbacks` object and preserves omitted providers. A null or empty model clears that entry. Unknown providers, foreign model families and invalid/unavailable new catalog selections are rejected without writing. Legacy global fields are not automatically migrated. The second settings panel is explicitly for legacy CLI runs and has no effect on native routing.

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

Copy the repository contents into the `jev-router` skill directory, or copy `SKILL.md`, `scripts/`, and `dashboard/` into an existing installation. The skill instructs the parent agent to read the actual `run.json`, verify worker output, and disclose the observed provider/model/usage in its final response.

On Windows, a typical destination is `%USERPROFILE%\.codex\skills\jev-router`.

## Privacy and security

- Binds only to loopback and refuses public/network binds.
- Run history and reports remain read-only. Only `PUT /api/config` is writable; accepted fields are `native_fallbacks`, the three legacy fallback fields and `confidence_threshold`.
- Config writes require same-origin JSON requests, validate enabled providers and efforts, and use atomic file replacement.
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
# Policy editor

The legacy CLI confidence threshold is editable from 0 to 100 percent (default 55%). The API stores it as a number from 0 to 1. Higher thresholds trigger legacy fallback more often. It does not govern the native skill's local model choice. Changes never rewrite historical runs.

On page load the model dropdown reads installed CLI catalogs: Codex models_cache.json (CODEX_HOME when set), Grok models_cache.json, and the newest Claude cache/model-catalog file. It filters by provider and excludes hidden entries. Only model identifiers and labels are exposed; no inference CLI, credential file, or remote model request is used.

These are cached catalogs, not a live account-entitlement check. The UI shows cache time and preserves an existing configured model when it is absent from the catalog. For missing/stale catalogs, refresh the relevant CLI's catalog and reload the page. Choosing another provider requires an explicit model selection.
# Prompt and execution provenance

New router runs retain top-level `user_prompt` (the original user instruction), `origin_provider`, optional `parent_agent`, and the run-time confidence threshold. Each task retains its rewritten `prompt`, dependency-expanded `expanded_prompt`, and `execution.submitted_prompt` (the exact text handed to the CLI, not the CLI's internally added system/skill context).

The always-open lineage view and SVG dependency graph distinguish Jev recommendation, fallback/override, actual execution provider, requested model, observed model IDs, session IDs, and PID. A requested alias is never presented as an observed model. Missing historical originals/telemetry remain unrecorded; a legacy prompt.txt is read only when confined to its run directory. Prompt text is redacted for display, with explicit truncation notices for expanded/submitted text over 100,000 characters. SHA-256 describes the original stored submitted text, before display redaction.
