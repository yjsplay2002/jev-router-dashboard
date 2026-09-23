#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit hook: route this turn's reasoning effort, in the same session.

No subagent, no proxy, no model change. The hook reads the prompt the user just
submitted, asks Jev once for the minimum sufficient effort under a 1.0 second
wall-clock cap, shows the decision, and tells the model to work at that depth.

No host lets a hook set the effort parameter itself, so the apply step is the
host's own command and the hook prints it ready to use. The behavioural half
(`additionalContext`) is what works on all three hosts today. With
`"auto_apply_effort": true` in config.json the hook instead writes a routed level
into the host's own settings file (Claude `effortLevel`, Codex
`model_reasoning_effort`), which the host reads on a later turn or session.

The hook never blocks a turn: any failure exits 0 with no output.
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_effort  # noqa: E402 - sibling module, loaded after sys.path is set

# How each host applies an effort level mid-session. None of them accept it from a hook.
APPLY_HINT = {
    "claude": "/effort {effort}",
    "grok": "/effort {effort}",
    "codex": "Alt+. / Alt+, (or /model)",
}
# Where each host keeps its default effort. Grok has no file-based setting.
SETTINGS = {
    "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json",
    "codex": Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml",
}
DEPTH = {
    "low": "Answer directly. Do not explore alternatives or restate the problem.",
    "medium": "Do the ordinary amount of checking: read what the change touches, then act.",
    "high": "Reason carefully before acting: trace the real flow, consider failure modes, verify claims.",
    "xhigh": "Treat a wrong answer as expensive: verify every assumption against primary sources before acting.",
    "max": "Exhaust the reasoning: verify every assumption, enumerate failure modes, prove the conclusion.",
}


def write_record(result: dict[str, object], prompt: str, provider: str, session: str) -> None:
    """One run record per routed turn, in the schema the dashboard already reads."""
    home = jev_effort.router_home()
    stamp = datetime.now(timezone.utc)
    run_id = f"{stamp:%Y%m%dT%H%M%SZ}-effort-{(session or uuid.uuid4().hex)[:8]}"
    directory = home / "runs" / run_id
    fragment = jev_effort.record(result)
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
                "result": f"effort {result['effort']} applied to the parent turn; model inherited",
            },
        }],
    }
    for key in ("router_calls", "router_usage"):
        if key in fragment:
            record[key] = fragment[key]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def replace_atomically(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.jev-{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8", newline="")  # keep the file's own line endings
    os.replace(temp, path)


def apply_effort(provider: str, effort: str) -> bool:
    """Write the routed level into the host's settings file; True only if the file now holds it."""
    path = SETTINGS.get(provider)
    if path is None or not path.is_file():
        return False
    with path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()
    eol = "\r\n" if "\r\n" in text else "\n"
    if provider == "claude":
        settings = json.loads(text)
        if not isinstance(settings, dict):
            return False
        if settings.get("effortLevel") != effort:
            settings["effortLevel"] = effort
            replace_atomically(path, json.dumps(settings, ensure_ascii=False, indent=2).replace("\n", eol) + eol)
        return True
    # Codex: only the top-level key, which sits before the first [table] header.
    table = re.search(r"^\[", text, re.M)
    cut = table.start() if table else len(text)
    head, tail = text[:cut], text[cut:]
    line = f'model_reasoning_effort = "{effort}"'
    key = re.compile(r"^model_reasoning_effort[ \t]*=[^\r\n]*", re.M)
    head = key.sub(line, head, count=1) if key.search(head) else f"{line}{eol}{head}"
    new_text = head + tail
    if new_text != text:
        replace_atomically(path, new_text)
    return True


def main() -> int:
    if os.environ.get("JEV_ROUTER_CHILD") == "1":
        return 0
    provider = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in jev_effort.PROVIDERS else "codex"
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

    hint = APPLY_HINT.get(provider, "/effort {effort}").format(effort=result["effort"])
    message = f"{jev_effort.announce(result)} -> apply: {hint}"
    if config.get("auto_apply_effort") is True and result["routed"]:
        try:
            if apply_effort(provider, str(result["effort"])):
                message = (f"{jev_effort.announce(result)} -> written to {SETTINGS[provider].name}; "
                           "takes effect when the host rereads it")
        except (OSError, ValueError):
            pass  # an unreadable settings file keeps the printed command
    context = (f"Routed effort for this turn: {result['effort']} "
               f"({'jev' if result['routed'] else 'your configured default'}). "
               f"{DEPTH.get(str(result['effort']), '')} "
               "The model is unchanged; do not propose switching it. "
               "This note is in English only for the model: reply in the language of the user's prompt.")

    try:
        write_record(result, prompt[:12000], provider, str(event.get("session_id") or ""))
    except OSError:
        pass  # a record is evidence, not a precondition

    print(json.dumps({
        "continue": True,
        "systemMessage": message,
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context},
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a routing hint must never break the user's turn
        sys.exit(0)
