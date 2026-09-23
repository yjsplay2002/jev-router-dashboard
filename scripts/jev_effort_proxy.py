#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Loopback proxy that applies the hook's routed effort to the very prompt it was routed for.

Claude Code reaches it through ANTHROPIC_BASE_URL (`/v1/...` -> api.anthropic.com) and
Codex through a model provider whose base_url ends in `/codex` (`/codex/...` ->
chatgpt.com/backend-api/codex). The UserPromptSubmit hook runs before the first request
of a turn and writes `<router home>/effort/<session id>`; this proxy reads that file and
sets the effort field (Claude `output_config.effort`, Codex `reasoning.effort`) on
requests carrying the same session header. Nothing else in the request changes, the
model is never touched, and effort is not part of the prompt-cache key.

A request is rewritten only when the CLI already sent an effort (the model supports it)
and the routed level is one of the configured `efforts`. Everything else passes through.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jev_effort  # noqa: E402

PORT = int(os.environ.get("JEV_EFFORT_PROXY_PORT") or 8791)
CODEX_PREFIX = "/codex"
# host -> (upstream host, upstream path prefix, session header, effort field)
ROUTES = {
    "claude": ("api.anthropic.com", "", "x-claude-code-session-id", "output_config"),
    "codex": ("chatgpt.com", "/backend-api/codex", "session-id", "reasoning"),
}
HOP = {"host", "connection", "content-length", "transfer-encoding", "keep-alive", "proxy-connection"}


def routed_effort(session: str) -> str | None:
    """The level the hook routed for this session's current turn, if any."""
    if not session or not session.replace("-", "").isalnum():
        return None
    home = jev_effort.router_home()
    try:
        level = (home / "effort" / session).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return level if level in jev_effort.allowed_efforts(jev_effort.read_config(home / "config.json")) else None


def rewrite(body: bytes, level: str | None, field: str = "output_config") -> bytes:
    if level is None or not body:
        return body
    try:
        request = json.loads(body)
    except ValueError:
        return body
    config = request.get(field) if isinstance(request, dict) else None
    if not isinstance(config, dict) or "effort" not in config or config["effort"] == level:
        return body
    config["effort"] = level
    return json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def note_applied(session: str, level: str, changed: bool) -> None:
    """Evidence that a request left with the routed level; no prompt content is ever written."""
    # ponytail: append-only log, rotate if it ever grows large enough to matter
    line = json.dumps({"t": round(time.time(), 3), "session": session[:8], "effort": level, "changed": changed})
    try:
        with (jev_effort.router_home() / "effort" / "applied.log").open("a", encoding="utf-8") as log:
            log.write(line + "\n")
    except OSError:
        pass


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    """Claude and Codex send a length; a chunked body is accepted so it is not forwarded empty."""
    raw_length = handler.headers.get("Content-Length")
    if raw_length is not None and raw_length.strip() != "":
        return handler.rfile.read(int(raw_length))
    if "chunked" not in handler.headers.get("Transfer-Encoding", "").lower():
        return b""
    parts: list[bytes] = []
    while True:
        line = handler.rfile.readline()
        if not line:
            break
        size = int(line.split(b";", 1)[0], 16)
        if size == 0:
            while True:
                trailer = handler.rfile.readline()
                if trailer in (b"\r\n", b"\n", b""):
                    break
            break
        parts.append(handler.rfile.read(size))
        if handler.rfile.read(2) != b"\r\n":
            break
    return b"".join(parts)


def chunk_block(data: bytes) -> bytes:
    return f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n"


CHUNK_END = b"0\r\n\r\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # a 1.0 close, or a 1.1 body with no length, is ECONNRESET to Claude Code

    def relay(self) -> None:
        started = False
        chunked = False
        finished = False
        upstream = None
        try:
            body = read_body(self)
            codex = self.path == CODEX_PREFIX or self.path.startswith(CODEX_PREFIX + "/")
            host, prefix, session_header, field = ROUTES["codex" if codex else "claude"]
            path = prefix + (self.path[len(CODEX_PREFIX):] if codex else self.path)
            if self.command == "POST" and (path.startswith("/v1/messages") or path.endswith("/responses")):
                session = self.headers.get(session_header, "")
                level = routed_effort(session)
                new_body = rewrite(body, level, field)
                if level is not None:
                    note_applied(session, level, changed=new_body is not body)
                body = new_body
            # ponytail: one upstream TLS connection per request; pool connections if the handshake shows up in latency
            upstream = http.client.HTTPSConnection(host, timeout=900)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            headers["Host"] = host
            headers["Content-Length"] = str(len(body))
            upstream.request(self.command, path, body=body, headers=headers)
            response = upstream.getresponse()
            length = response.getheader("Content-Length")
            close = self.request_version != "HTTP/1.1" or "close" in self.headers.get("Connection", "").lower()
            self.close_connection = close
            self.send_response(response.status, response.reason)
            started = True
            for key, value in response.getheaders():
                if key.lower() in HOP or key.lower() in ("content-length", "transfer-encoding", "date", "server"):
                    continue
                try:
                    key.encode("latin-1")
                    value.encode("latin-1")
                except UnicodeEncodeError:
                    continue
                self.send_header(key, value)
            if length is not None and self.command != "HEAD":
                self.send_header("Content-Length", length)
            elif self.command != "HEAD":
                self.send_header("Transfer-Encoding", "chunked")
                chunked = True
            self.send_header("Connection", "close" if close else "keep-alive")
            self.end_headers()
            if self.command == "HEAD":
                return
            if length is not None:
                remaining = int(length)
                while remaining:
                    chunk = response.read1(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                if remaining:
                    self.close_connection = True
            else:
                while chunk := response.read1(65536):
                    self.wfile.write(chunk_block(chunk))
                self.wfile.write(CHUNK_END)
                finished = True
            self.wfile.flush()
        except Exception:
            self.close_connection = True
            if started and chunked and not finished:
                try:
                    self.wfile.write(CHUNK_END)
                    self.wfile.flush()
                except Exception:
                    pass
            if not started:
                message = b'{"type":"error","error":{"type":"api_error","message":"jev effort proxy upstream failed"}}'
                try:
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(message)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(message)
                    self.wfile.flush()
                except Exception:
                    pass
        finally:
            if upstream is not None:
                upstream.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = relay

    def log_message(self, *_args) -> None:
        pass  # prompts pass through here; never log them


def main() -> int:
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
