#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit hook: route this turn's reasoning effort, in the same session.

No subagent, no model change. The hook reads the prompt the user just submitted,
asks Jev once for the minimum sufficient effort under a 1.0 second wall-clock cap,
shows the decision, and tells the model to work at that depth.

No host lets a hook set the effort parameter itself. On Claude Code the numeric
level is applied to this very prompt by `jev_effort_proxy.py`: when
ANTHROPIC_BASE_URL points at it, the hook starts it if needed and writes the
routed level to `<router home>/effort/<session id>`, which the proxy puts on the
turn's requests. Elsewhere the hook prints the host's own apply command.

The hook never blocks a turn: any failure exits 0 with no output.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_effort  # noqa: E402 - sibling module, loaded after sys.path is set
import jev_effort_proxy  # noqa: E402

# How each host applies an effort level mid-session. None of them accept it from a hook.
APPLY_HINT = {
    "claude": "/effort {effort}",
    "codex": "Alt+. / Alt+, (or /model)",
}
DEPTH = {
    "low": "Answer directly. Do not explore alternatives or restate the problem.",
    "medium": "Do the ordinary amount of checking: read what the change touches, then act.",
    "high": "Reason carefully before acting: trace the real flow, consider failure modes, verify claims.",
    "xhigh": "Treat a wrong answer as expensive: verify every assumption against primary sources before acting.",
    "max": "Exhaust the reasoning: verify every assumption, enumerate failure modes, prove the conclusion.",
}
PROXY_URL = f"http://127.0.0.1:{jev_effort_proxy.PORT}"


def proxy_in_use(provider: str) -> bool:
    """Claude: ANTHROPIC_BASE_URL is the proxy. Codex: config.toml selects the `jev` model provider."""
    if provider == "claude":
        return os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/") == PROXY_URL
    if provider != "codex":
        return False
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    try:
        top = re.split(r"^\[", path.read_text(encoding="utf-8"), maxsplit=1, flags=re.M)[0]
    except OSError:
        return False
    return re.search(r'^model_provider\s*=\s*"jev"\s*$', top, re.M) is not None


def proxy_listening() -> bool:
    try:
        socket.create_connection(("127.0.0.1", jev_effort_proxy.PORT), timeout=0.2).close()
        return True
    except OSError:
        return False


def ensure_proxy() -> None:
    """Every API call of the session goes through the proxy, so it must be up before the first one."""
    if proxy_listening():
        return
    flags = 0x00000008 | 0x00000200 | 0x08000000 if os.name == "nt" else 0  # detached, new group, no window
    subprocess.Popen([sys.executable, str(Path(jev_effort_proxy.__file__).resolve())],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, start_new_session=os.name != "nt", close_fds=True)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not proxy_listening():
        time.sleep(0.05)


def set_turn_effort(session: str, effort: str | None) -> bool:
    """Point this session's upcoming requests at `effort`; None hands the level back to the CLI."""
    if not session or not session.replace("-", "").isalnum():
        return False
    path = jev_effort.router_home() / "effort" / session
    if effort is None:
        path.unlink(missing_ok=True)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{session}.{os.getpid()}.tmp")
    temp.write_text(effort, encoding="utf-8")
    os.replace(temp, path)
    return True


def write_record(result: dict[str, object], prompt: str, provider: str, session: str, applied: bool) -> None:
    """One run record per routed turn, in the schema the dashboard already reads."""
    home = jev_effort.router_home()
    stamp = datetime.now(timezone.utc)
    run_id = f"{stamp:%Y%m%dT%H%M%SZ}-effort-{(session or uuid.uuid4().hex)[:8]}"
    directory = home / "runs" / run_id
    fragment = jev_effort.record(result)
    outcome = ("applied to this prompt's requests by the effort proxy" if applied
               else "shown to the model; the numeric level was not applied")
    record = {
        "run_id": run_id,
        "mode": "turn_effort",
        "execution_backend": "same_session",
        "status": "completed",
        "started_at": stamp.isoformat(),
        "origin_provider": provider,
        "parent_agent": "",
        "user_prompt": prompt,
        "tasks": [{
            "id": "turn",
            "title": "in-session effort routing",
            "provider": provider,
            "depends_on": [],
            "route": fragment["route"],
            "execution": {
                "status": "completed",
                "requested_effort": result["effort"],
                "actual_model": None,
                "provider": provider,
                "model_evidence_source": "user_prompt_submit_hook",
                "elapsed_seconds": result["elapsed_seconds"],
                "result": (f"effort {result['effort']} {outcome}; model inherited" if result["effort"]
                           else "not routed; the CLI's own effort level ran this prompt; model inherited"),
            },
        }],
    }
    for key in ("router_calls", "router_usage"):
        if key in fragment:
            record[key] = fragment[key]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    provider = sys.argv[1] if len(sys.argv) > 1 else "codex"
    if provider not in jev_effort.PROVIDERS:
        return 0  # an unsupported host (e.g. a leftover Grok hook) is never routed as another provider
    proxied = proxy_in_use(provider)
    if proxied:
        ensure_proxy()  # before any early return: slash commands and child sessions call the API too
    if "--session-start" in sys.argv or os.environ.get("JEV_ROUTER_CHILD") == "1":
        return 0
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:  # a Korean prompt on a cp949 console must not silently kill the hook
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return 0
    prompt = event.get("prompt") if isinstance(event, dict) else None
    if not isinstance(prompt, str) or not prompt.strip() or prompt.lstrip().startswith("/"):
        return 0  # nothing to route: empty prompt, or a command the host handles itself

    home = jev_effort.router_home()
    config = jev_effort.read_config(home / "config.json")
    result = jev_effort.build(prompt, provider, config, jev_effort.read_env(home / ".env"), 1.0)
    session = str(event.get("session_id") or "")

    applied = False
    if proxied:
        try:  # a fallback turn clears the file so a stale level never outlives its turn
            applied = set_turn_effort(session, str(result["effort"]) if result["effort"] else None)
        except OSError:
            applied = False
    if not result["effort"]:
        # Jev failed and no default gear is set: route nothing, add no instruction; the CLI's own level runs the turn.
        message = jev_effort.announce(result)
    elif applied:
        message = f"{jev_effort.announce(result)} -> applied to this prompt"
    else:
        hint = APPLY_HINT.get(provider, "/effort {effort}").format(effort=result["effort"])
        message = f"{jev_effort.announce(result)} -> apply: {hint}"
    context = (f"Routed effort for this turn: {result['effort']} "
               f"({'jev' if result['routed'] else 'your default gear'}). "
               f"{DEPTH.get(str(result['effort']), '')} "
               "The model is unchanged; do not propose switching it. "
               "This note is in English only for the model: reply in the language of the user's prompt.")

    try:
        write_record(result, prompt[:12000], provider, session, applied)
    except OSError:
        pass  # a record is evidence, not a precondition

    output: dict[str, object] = {"continue": True, "systemMessage": message}
    if result["effort"]:
        output["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": context}
    print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a routing hint must never break the user's turn
        sys.exit(0)
