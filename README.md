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
- an always-open fallback policy editor for provider, model, and reasoning effort.

Probability records are normalized whether Jev stored them as fractions (`0.73`) or percentages (`73`), so both labels and gauges render as `73%`. The dashboard reads existing `~/.config/jev-router/runs/*/run.json` files and never modifies run data. Its only write operation atomically updates `fallback_provider`, `fallback_model`, and `fallback_effort` in Jev's local `config.json`; unrelated settings are preserved. It does not call external services or require a database.

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
- Run history and reports remain read-only. Only `PUT /api/config` is writable, and only the three fallback fields are accepted.
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
