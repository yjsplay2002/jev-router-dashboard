#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Local dashboard for Jev router history and fallback policy."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import socket
import sys
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

VERSION = "0.5.0"
PROXY_PORT = int(os.environ.get("JEV_EFFORT_PROXY_PORT") or 8791)
TURN_SESSION_RE = re.compile(r"-effort-([0-9a-f]{8})$")
# ponytail: list price of jev-latest (USD per million tokens, checked 2026-09-23); Jev returns tokens, not cost.
# Move to config.json if TypeSafe changes it or bills differently per account.
JEV_PRICE_PER_MTOK = {"input": 0.042, "output": 0.0}
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


def default_config_path() -> Path:
    home = os.environ.get("JEV_ROUTER_HOME")
    if home:
        return Path(home).expanduser().resolve() / "config.json"
    return (Path.home() / ".config" / "jev-router" / "config.json").resolve()


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


def normalize_probability(value: object) -> float:
    """Accept Jev probability records expressed as either 0..1 or 0..100."""
    number = safe_number(value)
    if number > 1:
        number /= 100
    return max(0, min(1, number))


def normalize_usage(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raw = {}
    keys = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens", "reasoning_tokens")
    result = {key: max(0, int(safe_number(raw.get(key)))) for key in keys}
    result["total_tokens"] = max(0, int(safe_number(raw.get("total_tokens")))) or (
        result["input_tokens"] + result["output_tokens"]
    )
    return result


def normalize_task(task: object, include_content: bool = False, run_dir: Path | None = None) -> dict[str, object]:
    if not isinstance(task, dict):
        task = {}
    route = task.get("route") if isinstance(task.get("route"), dict) else {}
    execution = task.get("execution") if isinstance(task.get("execution"), dict) else {}
    probabilities = route.get("probabilities") if isinstance(route.get("probabilities"), dict) else {}
    result = execution.get("result", "")
    submitted = execution.get("submitted_prompt")
    submitted_source = "run.json" if isinstance(submitted, str) else ""
    if not isinstance(submitted, str) and run_dir and execution.get("prompt_path"):
        try:
            prompt_path = Path(str(execution["prompt_path"])).resolve()
            if prompt_path.name == "prompt.txt" and prompt_path.is_relative_to(run_dir.resolve()) and prompt_path.stat().st_size <= 1024 * 1024:
                submitted = prompt_path.read_text(encoding="utf-8")
                submitted_source = "prompt.txt (legacy execution artifact)"
        except (OSError, ValueError):
            pass
    models = execution.get("actual_models")
    models = [scrub(m, 160) for m in models if isinstance(m, str)] if isinstance(models, list) else []
    if execution.get("actual_model") and execution["actual_model"] not in models:
        models.append(scrub(execution["actual_model"], 160))
    normalized: dict[str, object] = {
        "id": scrub(task.get("id") or task.get("task"), 160),
        "title": scrub(task.get("title") or task.get("task") or task.get("id"), 220),
        "difficulty": scrub(task.get("difficulty"), 80),
        "category": scrub(task.get("category"), 80),
        "provider": scrub(task.get("provider") or execution.get("provider"), 80),
        "effort": scrub(route.get("effort") or execution.get("requested_effort") or task.get("requested_effort"), 80),
        "selected_by_jev": scrub(route.get("selected_by_jev"), 120),
        "confidence": normalize_probability(route.get("confidence")),
        "difficulty_confidence": normalize_probability(route.get("difficulty_confidence")),
        "category_confidence": normalize_probability(route.get("category_confidence")),
        "probabilities": {
            scrub(key, 80): normalize_probability(value)
            for key, value in probabilities.items()
        },
        "prompt": scrub(task.get("prompt"), 50000),
        "expanded_prompt": scrub(task.get("expanded_prompt"), 100000),
        "submitted_prompt": scrub(submitted, 100000),
        "submitted_prompt_source": submitted_source,
        "submitted_prompt_truncated": isinstance(submitted, str) and len(submitted) > 100000,
        "expanded_prompt_truncated": isinstance(task.get("expanded_prompt"), str) and len(task["expanded_prompt"]) > 100000,
        "submitted_prompt_sha256": scrub(execution.get("submitted_prompt_sha256"), 64),
        "execution_provider": scrub(execution.get("provider"), 80),
        "session_ids": [scrub(v, 160) for v in execution.get("session_ids", []) if isinstance(v, str)] if isinstance(execution.get("session_ids"), list) else [scrub(v, 160) for v in (execution.get("agent_id"), task.get("agent_id")) if isinstance(v, str)],
        "process_id": execution.get("process_id") if isinstance(execution.get("process_id"), int) else None,
        "actual_models": models,
        "model_evidence_source": scrub(execution.get("model_evidence_source") or execution.get("tool") or task.get("native_tool"), 160),
        "model_observation": "multiple" if len(models) > 1 else "observed" if models else "unknown",
        "execution_override": scrub(route.get("execution_override"), 80),
        "depends_on": [scrub(value, 160) for value in task.get("depends_on", []) if isinstance(value, str)]
        if isinstance(task.get("depends_on"), list) else [],
        "fallback": bool(route.get("fallback")),
        "fallback_reason": scrub(route.get("fallback_reason"), 160),
        "model_source": scrub(route.get("model_source"), 120),
        "effort_source": scrub(route.get("effort_source"), 120),
        "status": scrub(execution.get("status") or task.get("status") or ("completed" if task.get("outcome") else ""), 80),
        "actual_model": scrub(execution.get("actual_model") or task.get("observed_model"), 160),
        "requested_model": scrub(execution.get("requested_model") or route.get("requested_model") or task.get("model") or task.get("requested_model"), 160),
        "elapsed_seconds": max(0, safe_number(execution.get("elapsed_seconds"))),
        "exit_code": execution.get("exit_code"),
        "usage": normalize_usage(execution.get("usage")),
        "result_summary": scrub(result or task.get("outcome"), 600),
    }
    if include_content:
        normalized["result"] = scrub(result, 10000)
    return normalized


def router_cost(usage: dict[str, int]) -> float:
    """What the recorded Jev call cost at list price; 0 when no call was made (no key, or the cap hit first)."""
    return (usage.get("input_tokens", 0) * JEV_PRICE_PER_MTOK["input"]
            + usage.get("output_tokens", 0) * JEV_PRICE_PER_MTOK["output"]) / 1_000_000


def normalize_run(raw: object, path: Path, include_content: bool = False) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("run.json root must be an object")
    tasks = raw.get("tasks") if isinstance(raw.get("tasks"), list) else raw.get("workers") if isinstance(raw.get("workers"), list) else []
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
        "status": scrub(raw.get("status") or ("completed" if tasks else ""), 80),
        "started_at": scrub(raw.get("started_at") or raw.get("recorded_at") or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), 80),
        "elapsed_seconds": max(0, safe_number(raw.get("elapsed_seconds"))),
        "policy_version": scrub(raw.get("policy_version"), 160),
        "router_model": router_model,
        "user_prompt": scrub(raw.get("user_prompt"), 200000),
        "origin_provider": scrub(raw.get("origin_provider"), 80),
        "parent_agent": scrub(raw.get("parent_agent") or raw.get("parent_identity"), 200),
        "confidence_threshold": normalize_probability(raw.get("confidence_threshold")) if raw.get("confidence_threshold") is not None else None,
        "router_usage": (router_usage := normalize_usage(raw.get("router_usage"))),
        "router_cost_usd": router_cost(router_usage),
        "task_count": len(tasks),
        "tasks": [normalize_task(task, include_content, path.parent) for task in tasks],
        "has_report": has_report,
        "source_mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
    }


def read_applied_log(path: Path, limit: int = 20000) -> list[dict[str, object]]:
    """The effort proxy's per-request log: session prefix, level sent, and whether it changed the CLI's value."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and isinstance(row.get("session"), str) and isinstance(row.get("effort"), str):
            entries.append({"t": safe_number(row.get("t")), "session": row["session"][:8],
                            "effort": scrub(row["effort"], 20), "changed": bool(row.get("changed"))})
    return entries


def attach_proxy_evidence(runs: list[dict[str, object]], entries: list[dict[str, object]]) -> list[dict[str, object]]:
    """Give each hook turn the proxy requests that followed it in the same session, until that session's next turn."""
    turns: dict[str, list[tuple[float, dict[str, object]]]] = {}
    for run in runs:
        match = TURN_SESSION_RE.search(str(run.get("run_id", "")))
        if run.get("mode") != "turn_effort" or not match:
            continue
        try:
            started = datetime.fromisoformat(str(run.get("started_at"))).timestamp()
        except ValueError:
            continue
        turns.setdefault(match[1], []).append((started, run))
    counts: dict[int, dict[str, object]] = {}
    for items in turns.values():
        items.sort(key=lambda item: item[0])
    for entry in entries:
        items = turns.get(str(entry["session"]))
        owner = None
        for started, run in items or []:
            if started <= float(entry["t"]) + 1:  # clocks agree; 1s covers rounding
                owner = run
        if owner is None:
            continue
        proof = counts.setdefault(id(owner), {"requests": 0, "changed": 0, "efforts": {}})
        proof["requests"] += 1
        proof["changed"] += int(bool(entry["changed"]))
        proof["efforts"][entry["effort"]] = proof["efforts"].get(entry["effort"], 0) + 1
    tracked = {id(run) for items in turns.values() for _, run in items}
    empty = {"requests": 0, "changed": 0, "efforts": {}}
    # copies, never the cached records; a turn with no proxied request gets an explicit zero
    return [{**run, "proxy": counts.get(id(run), empty)} if id(run) in tracked else run for run in runs]


def proxy_listening(port: int = PROXY_PORT) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        return True
    except OSError:
        return False


class RunStore:
    def __init__(self, root: Path, max_runs: int = 500, include_content: bool = False):
        self.root = root.expanduser().resolve()
        self.max_runs = max(1, max_runs)
        self.include_content = include_content
        self._cache: dict[Path, tuple[int, int, dict[str, object]]] = {}
        self._cache_lock = threading.Lock()

    def _paths(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        paths = [path for path in self.root.glob("*/run.json") if path.is_file()]
        paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return paths[: self.max_runs]

    def read_path(self, path: Path) -> dict[str, object]:
        try:
            stat = path.stat()
            cache_key = (stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            return self._unreadable(path, exc)
        with self._cache_lock:
            cached = self._cache.get(path)
            if cached and cached[:2] == cache_key:
                return cached[2]
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                normalized = normalize_run(raw, path, self.include_content)
                self._cache[path] = (*cache_key, normalized)
                return normalized
            except Exception as exc:  # a writer may be replacing run.json while we poll
                if cached:
                    stale = dict(cached[2])
                    stale["stale"] = True
                    return stale
                return self._unreadable(path, exc)

    @staticmethod
    def _unreadable(path: Path, exc: Exception) -> dict[str, object]:
        try:
            source_mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            source_mtime = datetime.now(timezone.utc).isoformat()
        return {
            "run_id": path.parent.name,
            "status": "unreadable",
            "error": scrub(exc, 300),
            "started_at": "",
            "elapsed_seconds": 0,
            "task_count": 0,
            "tasks": [],
            "has_report": False,
            "source_mtime": source_mtime,
        }

    def list(self) -> list[dict[str, object]]:
        return attach_proxy_evidence([self.read_path(path) for path in self._paths()],
                                     read_applied_log(self.root.parent / "effort" / "applied.log"))

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


class ConfigStore:
    """Expose and update only the per-provider fallback effort in Jev's config."""

    NATIVE_PROVIDERS = ("codex", "claude")
    DEFAULT_EFFORTS = ("low", "medium", "high")

    def __init__(self, path: Path, model_home: Path | None = None):
        self.path = path.expanduser().resolve()
        self.model_home = model_home or Path.home()
        self._lock = threading.Lock()

    @classmethod
    def effort_options(cls, raw: dict[str, object]) -> list[str]:
        """The effort levels this installation routes between, from Jev's own config."""
        efforts = raw.get("efforts")
        if isinstance(efforts, list):
            valid = [e for e in efforts if isinstance(e, str) and e and len(e) <= 40]
            if valid:
                return list(dict.fromkeys(valid))
        return list(cls.DEFAULT_EFFORTS)

    @classmethod
    def _native_fallbacks(cls, raw: dict[str, object]) -> dict[str, dict[str, str | None]]:
        stored = raw.get("native_fallbacks")
        stored = stored if isinstance(stored, dict) else {}
        options = cls.effort_options(raw)
        result: dict[str, dict[str, str | None]] = {}
        for provider in cls.NATIVE_PROVIDERS:
            entry = stored.get(provider)
            effort = entry.get("effort") if isinstance(entry, dict) else None
            result[provider] = {"effort": effort if isinstance(effort, str) and effort in options else None}
        return result

    def _read(self) -> dict[str, object]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Jev config not found: {self.path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read Jev config: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("Jev config must contain a JSON object")
        return raw

    def public(self, raw: dict[str, object] | None = None) -> dict[str, object]:
        raw = raw or self._read()
        return {
            "native_fallbacks": self._native_fallbacks(raw),
            "effort_options": self.effort_options(raw),
            "config_path": scrub(self.path),
        }

    def update(self, patch: object) -> dict[str, object]:
        if not isinstance(patch, dict):
            raise ValueError("Request body must be a JSON object")
        if set(patch) == {"native_fallbacks"}:
            return self._update_native(patch["native_fallbacks"])
        raise ValueError("Provide only native_fallbacks")

    def _update_native(self, patch: object) -> dict[str, object]:
        if not isinstance(patch, dict):
            raise ValueError("native_fallbacks must be an object")
        unknown = set(patch) - set(self.NATIVE_PROVIDERS)
        if unknown:
            raise ValueError(f"Unknown native fallback provider: {sorted(unknown)[0]}")
        with self._lock:
            raw = self._read()
            updated = self._native_fallbacks(raw)
            options = self.effort_options(raw)
            for provider, entry in patch.items():
                if not isinstance(entry, dict):
                    raise ValueError(f"native_fallbacks.{provider} must be an object")
                if set(entry) != {"effort"}:
                    raise ValueError(f"native_fallbacks.{provider} may contain only effort")
                effort = entry["effort"]
                if effort is not None and not isinstance(effort, str):
                    raise ValueError(f"native_fallbacks.{provider}.effort must be a string or null")
                if isinstance(effort, str):
                    effort = effort.strip() or None
                if effort is not None and effort not in options:
                    raise ValueError(f"Choose an effort from {options}")
                updated[provider] = {"effort": effort}
            raw["native_fallbacks"] = updated
            self._write(raw)
            return self.public(raw)

    def _write(self, raw: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(raw, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise ValueError(f"Could not write Jev config: {exc}") from exc
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR can allow two listeners to serve different API versions.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], store: RunStore, config: ConfigStore | None = None):
        self.store = store
        self.config = config or ConfigStore(default_config_path())
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
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src 'self' data:")
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
            self._json({"version": VERSION, "runs_dir": str(self.server.store.root), "run_count": len(self.server.store._paths()),
                        "proxy": {"port": PROXY_PORT, "listening": proxy_listening()},
                        "router_price_per_mtok": JEV_PRICE_PER_MTOK})
            return
        if path == "/api/config":
            try:
                self._json(self.server.config.public())
            except ValueError as exc:
                self._json({"error": scrub(exc, 300)}, HTTPStatus.SERVICE_UNAVAILABLE)
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
        types = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
        self._send(200, target.read_bytes(), types.get(target.suffix, "application/octet-stream"))

    def do_PUT(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._json({"error": "Host must be loopback"}, HTTPStatus.FORBIDDEN)
            return
        path = unquote(urlparse(self.path).path)
        if path != "/api/config":
            self._json({"error": "Method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)
            return
        if self.headers.get_content_type() != "application/json":
            self._json({"error": "Content-Type must be application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length < 1 or length > 16 * 1024:
            self._json({"error": "Request body must be between 1 byte and 16 KiB"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self._json(self.server.config.update(payload))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json({"error": "Request body must be valid UTF-8 JSON"}, HTTPStatus.BAD_REQUEST)
        except ValueError as exc:
            self._json({"error": scrub(exc, 300)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        self._json({"error": "Method not allowed"}, HTTPStatus.METHOD_NOT_ALLOWED)

    do_PATCH = do_POST
    do_DELETE = do_POST


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Browse Jev routing history on localhost")
    parser.add_argument("--runs-dir", type=Path, default=default_runs_dir())
    parser.add_argument("--config", type=Path, default=default_config_path(), help="Path to Jev config.json")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--max-runs", type=int, default=500)
    parser.add_argument("--include-content", action="store_true", help="Include full sanitized results in API responses")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.bind not in {"127.0.0.1", "localhost", "::1"}:
        print("Refusing non-loopback bind; this dashboard contains local routing metadata.", file=sys.stderr)
        return 2
    store = RunStore(args.runs_dir, args.max_runs, args.include_content)
    try:
        server = DashboardServer((args.bind, args.port), store, ConfigStore(args.config))
    except OSError as exc:
        print(f"Could not start dashboard: {exc}", file=sys.stderr)
        return 1
    url = f"http://{args.bind}:{server.server_port}"
    print(f"Jev dashboard: {url}")
    print(f"Runs: {store.root}")
    print(f"Config: {server.config.path}")
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
