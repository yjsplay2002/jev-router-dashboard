#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Local, read-only dashboard for Jev router run history."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import socket
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent.parent
STATIC_ROOT = HERE / "dashboard"
RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
SECRET_PATTERNS = (
    (re.compile(r"\b(?:gh[oprsu]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "<redacted:github-token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "<redacted:api-key>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*", re.I), "Bearer <redacted>"),
    (re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*[^\s,;]+"), r"\1=<redacted>"),
)


def default_runs_dir() -> Path:
    home = os.environ.get("JEV_ROUTER_HOME")
    if home:
        return Path(home).expanduser().resolve() / "runs"
    return (Path.home() / ".config" / "jev-router" / "runs").resolve()


def scrub(value: object, limit: int = 600) -> str:
    text = "" if value is None else str(value)
    try:
        home = str(Path.home())
        if home:
            text = text.replace(home, "~")
    except RuntimeError:
        pass
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def safe_number(value: object, default: float = 0) -> float:
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return number
    except (TypeError, ValueError):
        return default


def normalize_usage(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raw = {}
    keys = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens", "reasoning_tokens")
    result = {key: max(0, int(safe_number(raw.get(key)))) for key in keys}
    result["total_tokens"] = max(0, int(safe_number(raw.get("total_tokens")))) or (
        result["input_tokens"] + result["output_tokens"]
    )
    return result


def normalize_task(task: object, include_content: bool = False) -> dict[str, object]:
    if not isinstance(task, dict):
        task = {}
    route = task.get("route") if isinstance(task.get("route"), dict) else {}
    execution = task.get("execution") if isinstance(task.get("execution"), dict) else {}
    probabilities = route.get("probabilities") if isinstance(route.get("probabilities"), dict) else {}
    result = execution.get("result", "")
    normalized: dict[str, object] = {
        "id": scrub(task.get("id"), 160),
        "title": scrub(task.get("title"), 220),
        "difficulty": scrub(task.get("difficulty"), 80),
        "category": scrub(task.get("category"), 80),
        "provider": scrub(task.get("provider") or execution.get("provider"), 80),
        "effort": scrub(route.get("effort") or execution.get("requested_effort"), 80),
        "selected_by_jev": scrub(route.get("selected_by_jev"), 120),
        "confidence": max(0, min(1, safe_number(route.get("confidence")))),
        "difficulty_confidence": max(0, min(1, safe_number(route.get("difficulty_confidence")))),
        "category_confidence": max(0, min(1, safe_number(route.get("category_confidence")))),
        "probabilities": {
            scrub(key, 80): max(0, min(1, safe_number(value)))
            for key, value in probabilities.items()
        },
        "fallback": bool(route.get("fallback")),
        "fallback_reason": scrub(route.get("fallback_reason"), 160),
        "status": scrub(execution.get("status") or task.get("status"), 80),
        "actual_model": scrub(execution.get("actual_model"), 160),
        "requested_model": scrub(execution.get("requested_model"), 160),
        "elapsed_seconds": max(0, safe_number(execution.get("elapsed_seconds"))),
        "exit_code": execution.get("exit_code"),
        "usage": normalize_usage(execution.get("usage")),
        "result_summary": scrub(result, 600),
    }
    if include_content:
        normalized["prompt"] = scrub(task.get("prompt"), 5000)
        normalized["result"] = scrub(result, 10000)
    return normalized


def normalize_run(raw: object, path: Path, include_content: bool = False) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("run.json root must be an object")
    tasks = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    router_calls = raw.get("router_calls") if isinstance(raw.get("router_calls"), list) else []
    router_model = ""
    if router_calls and isinstance(router_calls[0], dict):
        router_model = scrub(router_calls[0].get("model"), 120)
    report = Path(str(raw.get("report", ""))) if raw.get("report") else None
    has_report = False
    if report:
        try:
            has_report = report.resolve().is_file() and report.resolve().is_relative_to(path.parent.resolve())
        except (OSError, ValueError):
            has_report = False
    return {
        "run_id": scrub(raw.get("run_id") or path.parent.name, 160),
        "mode": scrub(raw.get("mode"), 80),
        "status": scrub(raw.get("status"), 80),
        "started_at": scrub(raw.get("started_at"), 80),
        "elapsed_seconds": max(0, safe_number(raw.get("elapsed_seconds"))),
        "policy_version": scrub(raw.get("policy_version"), 160),
        "router_model": router_model,
        "router_usage": normalize_usage(raw.get("router_usage")),
        "task_count": len(tasks),
        "tasks": [normalize_task(task, include_content) for task in tasks],
        "has_report": has_report,
        "source_mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
    }


class RunStore:
    def __init__(self, root: Path, max_runs: int = 500, include_content: bool = False):
        self.root = root.expanduser().resolve()
        self.max_runs = max(1, max_runs)
        self.include_content = include_content

    def _paths(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        paths = [path for path in self.root.glob("*/run.json") if path.is_file()]
        paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return paths[: self.max_runs]

    def read_path(self, path: Path) -> dict[str, object]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return normalize_run(raw, path, self.include_content)
        except Exception as exc:  # malformed historical records should not take down the dashboard
            return {
                "run_id": path.parent.name,
                "status": "unreadable",
                "error": scrub(exc, 300),
                "started_at": "",
                "elapsed_seconds": 0,
                "task_count": 0,
                "tasks": [],
                "has_report": False,
            }

    def list(self) -> list[dict[str, object]]:
        return [self.read_path(path) for path in self._paths()]

    def get(self, run_id: str) -> dict[str, object] | None:
        if not RUN_ID_RE.fullmatch(run_id):
            return None
        candidate = (self.root / run_id / "run.json").resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return self.read_path(candidate) if candidate.is_file() else None

    def report(self, run_id: str) -> Path | None:
        if not RUN_ID_RE.fullmatch(run_id):
            return None
        run_file = (self.root / run_id / "run.json").resolve()
        if not run_file.is_file():
            return None
        try:
            raw = json.loads(run_file.read_text(encoding="utf-8"))
            report = Path(str(raw.get("report", ""))).resolve()
            report.relative_to(run_file.parent.resolve())
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return report if report.is_file() and report.stat().st_size <= 8 * 1024 * 1024 else None


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], store: RunStore):
        self.store = store
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self._headers(status, content_type, len(body))
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # Browsers may cancel a polling request while a page is closing.
            return

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        return host in {"127.0.0.1", "localhost", "::1"}

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._json({"error": "Host must be loopback"}, HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            runs = self.server.store.list()
            self._json({"version": VERSION, "runs_dir": str(self.server.store.root), "run_count": len(runs)})
            return
        if path == "/api/runs":
            query = parse_qs(parsed.query)
            limit = min(500, max(1, int(query.get("limit", ["200"])[0])))
            runs = self.server.store.list()[:limit]
            self._json({"runs": runs, "total": len(runs), "generated_at": datetime.now(timezone.utc).isoformat()})
            return
        if path.startswith("/api/runs/"):
            run_id = path.removeprefix("/api/runs/")
            run = self.server.store.get(run_id)
            self._json(run if run else {"error": "Run not found"}, 200 if run else 404)
            return
        if path.startswith("/reports/"):
            run_id = path.removeprefix("/reports/")
            report = self.server.store.report(run_id)
            if not report:
                self._json({"error": "Report not found"}, 404)
                return
            body = report.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return
        static_path = "index.html" if path == "/" else path.removeprefix("/static/")
        target = (STATIC_ROOT / static_path).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self._json({"error": "Not found"}, 404)
            return
        if not target.is_file():
            self._json({"error": "Not found"}, 404)
            return
        types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
        self._send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))

    def do_POST(self) -> None:  # noqa: N802
        self._json({"error": "Read-only server"}, HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse Jev routing history on localhost")
    parser.add_argument("--runs-dir", type=Path, default=default_runs_dir())
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--max-runs", type=int, default=500)
    parser.add_argument("--include-content", action="store_true", help="Include sanitized prompts and full results in API responses")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.bind not in {"127.0.0.1", "localhost", "::1"}:
        print("Refusing non-loopback bind; this dashboard contains local routing metadata.", file=sys.stderr)
        return 2
    store = RunStore(args.runs_dir, args.max_runs, args.include_content)
    try:
        server = DashboardServer((args.bind, args.port), store)
    except OSError as exc:
        print(f"Could not start dashboard: {exc}", file=sys.stderr)
        return 1
    url = f"http://{args.bind}:{server.server_port}"
    print(f"Jev dashboard: {url}")
    print(f"Runs: {store.root}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
