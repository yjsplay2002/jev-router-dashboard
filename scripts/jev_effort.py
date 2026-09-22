#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Route the reasoning effort for one bounded native subagent task.

The model is never chosen here. Switching models invalidates the host's prompt
cache, which costs more than a cheaper model saves, so the parent's current
model is always inherited unchanged. Only the reasoning effort is routed.

One bounded Jev decision picks the effort under a hard wall-clock cap
(1.0 second by default). If Jev is slower than the cap, unreachable, or
unconfigured, nothing is routed and the user's configured effort applies.
The script prints one line for the parent to echo, and exits 0 in every case
so a routing decision can never block the task.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "medium"
PROVIDERS = ("codex", "claude", "grok")
EFFORT_CRITERIA = {
    "low": "Mechanical or fully specified work: apply a stated change, run a known command, extract or reformat given text.",
    "medium": "Ordinary engineering work: implement a scoped task, write focused tests, review a small diff.",
    "high": "Hard reasoning: unknown root cause, cross-cutting design, concurrency or migration risk, ambiguous requirements.",
    "minimal": "Trivial lookup or single-line edit with nothing to decide.",
    "xhigh": "Exceptionally hard reasoning where a wrong answer is expensive to undo.",
}


def router_home() -> Path:
    override = os.environ.get("JEV_ROUTER_HOME")
    return Path(override).expanduser() if override else Path.home() / ".config" / "jev-router"


def read_env(path: Path) -> dict[str, str]:
    """Parse the local .env; only Jev credentials are read, nothing is printed."""
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def read_config(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def allowed_efforts(config: dict[str, object]) -> tuple[str, ...]:
    efforts = config.get("efforts")
    if isinstance(efforts, list):
        valid = tuple(e for e in efforts if isinstance(e, str) and e)
        if valid:
            return valid
    return DEFAULT_EFFORTS


def configured_effort(config: dict[str, object], provider: str, efforts: tuple[str, ...]) -> str:
    """The effort the user set for this provider; used whenever Jev does not answer in time."""
    entry = (config.get("native_fallbacks") or {}).get(provider) if isinstance(config.get("native_fallbacks"), dict) else None
    effort = entry.get("effort") if isinstance(entry, dict) else None
    if isinstance(effort, str) and effort in efforts:
        return effort
    return efforts[len(efforts) // 2] if efforts else DEFAULT_EFFORT


def ask_jev(state: str, efforts: tuple[str, ...], api_key: str, model: str, timeout: float) -> dict[str, object]:
    body = json.dumps({
        "state": state,
        "model": model,
        "questions": {
            "effort": {
                "type": "choice",
                "instructions": "What is the minimum reasoning effort that can complete this task correctly?",
                "criteria": {e: EFFORT_CRITERIA.get(e, e) for e in efforts},
            }
        },
    }).encode("utf-8")
    request = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS endpoint
        return json.loads(response.read().decode("utf-8"))


def decide(state: str, efforts: tuple[str, ...], api_key: str, model: str, timeout: float) -> tuple[dict[str, object] | None, float, str]:
    """Return (payload, elapsed, reason). The cap is wall clock, not a socket timeout."""
    box: dict[str, object] = {}
    def worker() -> None:
        try:
            box["payload"] = ask_jev(state, efforts, api_key, model, timeout)
        except Exception as exc:  # any transport or decode failure is a no-route, never a crash
            box["error"] = type(exc).__name__
    thread = threading.Thread(target=worker, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(timeout)
    elapsed = time.monotonic() - started
    if thread.is_alive():
        return None, elapsed, "timeout"
    if "error" in box:
        return None, elapsed, "error"
    if elapsed > timeout:
        return None, elapsed, "timeout"
    return box.get("payload"), elapsed, "jev"  # type: ignore[return-value]


def build(task: str, provider: str, config: dict[str, object], env: dict[str, str], timeout: float) -> dict[str, object]:
    efforts = allowed_efforts(config)
    default = configured_effort(config, provider, efforts)
    api_key = env.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_API_KEY") or ""
    model = env.get("TYPESAFE_MODEL") or os.environ.get("TYPESAFE_MODEL") or "jev-latest"
    limit = config.get("judge_context_chars")
    state = task[: limit if isinstance(limit, int) and limit > 0 else 12000]

    result: dict[str, object] = {
        "effort": default, "effort_source": "user_setting", "routed": False,
        "reason": "", "elapsed_seconds": 0.0, "cap_seconds": timeout,
        "model_source": "inherited_parent_model", "router_model": "", "confidence": None,
        "probabilities": {}, "usage": {},
    }
    if not api_key:
        result["reason"] = "no TYPESAFE_API_KEY"
        return result

    payload, elapsed, reason = decide(state, efforts, api_key, model, timeout)
    result["elapsed_seconds"] = round(elapsed, 3)
    if payload is None:
        result["reason"] = f"{elapsed:.2f}s > {timeout:.1f}s cap" if reason == "timeout" else "jev unreachable"
        return result

    answer = ((payload.get("answers") or {}).get("effort") or {}) if isinstance(payload, dict) else {}
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in efforts:
        result["reason"] = "jev returned no usable effort"
        return result

    result.update({
        "effort": choice, "effort_source": "jev", "routed": True,
        "router_model": payload.get("model") if isinstance(payload.get("model"), str) else model,
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities") if isinstance(answer.get("probabilities"), dict) else {},
        "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
    })
    return result


def announce(result: dict[str, object]) -> str:
    """One line the parent echoes verbatim before it dispatches."""
    if result["routed"]:
        confidence = result.get("confidence")
        confidence = f", conf {round(float(confidence) * 100)}%" if isinstance(confidence, (int, float)) else ""
        return (f"jev: effort={result['effort']} ({result['router_model']}, "
                f"{result['elapsed_seconds']:.2f}s{confidence}) - model unchanged")
    return (f"jev: not routed ({result['reason']}) - effort={result['effort']} "
            f"(your setting) - model unchanged")


def record(result: dict[str, object]) -> dict[str, object]:
    """The run.json fragment for this decision; paste it into the task's route."""
    fragment: dict[str, object] = {
        "route": {
            "effort": result["effort"],
            "effort_source": result["effort_source"],
            "model_source": result["model_source"],
        }
    }
    if result["routed"]:
        fragment["route"].update({  # type: ignore[union-attr]
            "selected_by_jev": result["effort"],
            "confidence": result["confidence"],
            "probabilities": result["probabilities"],
        })
        fragment["router_calls"] = [{"model": result["router_model"], "elapsed_seconds": result["elapsed_seconds"]}]
        if result["usage"]:
            fragment["router_usage"] = result["usage"]
    else:
        fragment["route"]["fallback"] = True  # type: ignore[index]
        fragment["route"]["fallback_reason"] = result["reason"]  # type: ignore[index]
    return fragment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Choose the reasoning effort for one native subagent task.")
    parser.add_argument("task", help="Bounded task description. No file contents, no secrets.")
    parser.add_argument("--provider", choices=PROVIDERS, default="codex", help="Parent provider (origin_provider)")
    parser.add_argument("--config", type=Path, default=None, help="Path to Jev config.json")
    parser.add_argument("--timeout", type=float, default=1.0, help="Wall-clock cap in seconds (default 1.0)")
    parser.add_argument("--json", action="store_true", help="Also print the run.json route fragment")
    args = parser.parse_args(argv)
    # A cp949/cp1252 console must not turn a Korean task description into a crash or "??".
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    home = router_home()
    config = read_config(args.config or home / "config.json")
    result = build(args.task, args.provider, config, read_env(home / ".env"), max(0.05, args.timeout))
    print(announce(result))
    if args.json:
        print(json.dumps(record(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
