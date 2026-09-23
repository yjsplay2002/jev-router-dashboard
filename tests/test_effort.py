# SPDX-License-Identifier: Apache-2.0
"""The effort router must never route the model and never outlive its 1 second cap."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jev_effort", ROOT / "scripts" / "jev_effort.py")
effort = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(effort)

CONFIG = {"efforts": ["low", "medium", "high"], "judge_context_chars": 200,
          "native_fallbacks": {"codex": {"effort": "medium"}, "claude": {"effort": None}}}
ENV = {"TYPESAFE_API_KEY": "test-key", "TYPESAFE_MODEL": "jev-latest"}


class EffortRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = effort.ask_jev

    def tearDown(self):
        effort.ask_jev = self.original
        self.temp.cleanup()

    def run_with(self, responder, timeout=1.0, provider="codex", config=CONFIG):
        effort.ask_jev = responder
        return effort.build("summarise this bounded task", provider, config, ENV, timeout)

    def test_slow_jev_falls_back_to_the_user_setting(self):
        def slow(*_args, **_kwargs):
            time.sleep(1.2)
            return {"model": "jev-1.13.0", "answers": {"effort": {"choice": "high"}}}
        started = time.monotonic()
        result = self.run_with(slow)
        self.assertLess(time.monotonic() - started, 1.15, "the cap must bound wall clock, not the request")
        self.assertFalse(result["routed"])
        self.assertEqual(result["effort"], "medium")
        self.assertEqual(result["effort_source"], "user_setting")
        self.assertIn("cap", result["reason"])
        self.assertIn("not routed", effort.announce(result))
        fragment = effort.record(result)
        self.assertTrue(fragment["route"]["fallback"])
        self.assertNotIn("router_calls", fragment)

    def test_fast_jev_routes_effort_and_leaves_the_model_alone(self):
        payload = {"model": "jev-1.13.0", "usage": {"input_tokens": 12, "output_tokens": 3},
                   "answers": {"effort": {"choice": "high", "confidence": 0.81,
                                          "probabilities": {"low": 0.0, "medium": 0.19, "high": 0.81}}}}
        result = self.run_with(lambda *a, **k: payload)
        self.assertTrue(result["routed"])
        self.assertEqual(result["effort"], "high")
        self.assertEqual(result["model_source"], "inherited_parent_model")
        line = effort.announce(result)
        self.assertIn("effort=high", line)
        self.assertIn("model unchanged", line)
        fragment = effort.record(result)
        self.assertEqual(fragment["route"]["effort_source"], "jev")
        self.assertEqual(fragment["router_calls"][0]["model"], "jev-1.13.0")
        self.assertEqual(fragment["router_usage"]["input_tokens"], 12)
        self.assertNotIn("requested_model", json.dumps(fragment))

    def test_unreachable_jev_and_missing_key_are_normal_outcomes(self):
        def boom(*_args, **_kwargs):
            raise OSError("connection refused")
        result = self.run_with(boom)
        self.assertFalse(result["routed"])
        self.assertEqual(result["effort"], "medium")
        self.assertIn("OSError", result["reason"])  # the cause survives, it is not flattened
        no_key = effort.build("task", "codex", CONFIG, {}, 1.0)
        self.assertEqual(no_key["reason"], "no TYPESAFE_API_KEY")
        self.assertEqual(no_key["effort"], "medium")

    def test_without_a_default_gear_a_failed_jev_leaves_the_cli_effort_alone(self):
        result = self.run_with(lambda *a, **k: {"answers": {"effort": {"choice": "extreme"}}}, provider="claude")
        self.assertFalse(result["routed"])
        self.assertIsNone(result["effort"])
        self.assertEqual(result["effort_source"], "host_setting")
        self.assertEqual(result["reason"], "jev returned no usable effort")
        self.assertIn("your CLI effort setting applies", effort.announce(result))

    def test_task_text_is_capped_by_judge_context_chars(self):
        seen = {}
        def capture(state, *_args, **_kwargs):
            seen["state"] = state
            return {"answers": {"effort": {"choice": "low"}}}
        effort.ask_jev = capture
        effort.build("x" * 5000, "codex", CONFIG, ENV, 1.0)
        self.assertEqual(len(seen["state"]), 200)



class HookTests(unittest.TestCase):
    """The hook must emit valid hook JSON, route the turn, and never block it."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("jev_effort_hook", ROOT / "scripts" / "jev_effort_hook.py")
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
        (self.home / ".env").write_text("TYPESAFE_API_KEY=test-key\n", encoding="utf-8")
        self._env = os.environ.get("JEV_ROUTER_HOME")
        os.environ["JEV_ROUTER_HOME"] = str(self.home)
        self._base = os.environ.pop("ANTHROPIC_BASE_URL", None)  # a real proxy setup must not leak in
        self._codex = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.home)  # no config.toml here, so Codex is not proxied

    def tearDown(self):
        if self._env is None:
            os.environ.pop("JEV_ROUTER_HOME", None)
        else:
            os.environ["JEV_ROUTER_HOME"] = self._env
        if self._base is not None:
            os.environ["ANTHROPIC_BASE_URL"] = self._base
        if self._codex is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self._codex
        self.temp.cleanup()

    def invoke(self, event, provider="claude", choice="high"):
        self.hook.jev_effort.ask_jev = lambda *a, **k: {
            "model": "jev-1.13.0", "answers": {"effort": {"choice": choice, "confidence": 0.9}}}
        out, err = io.StringIO(), io.StringIO()
        stdin, stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps(event)), out
        try:
            code = self.hook.main.__wrapped__() if hasattr(self.hook.main, "__wrapped__") else self.hook.main()
        finally:
            sys.stdin, sys.stdout = stdin, stdout
        return code, out.getvalue().strip(), err

    def test_routes_the_turn_and_names_the_host_apply_command(self):
        sys.argv = ["hook", "claude"]
        code, out, _ = self.invoke({"prompt": "trace why the migration drops rows", "session_id": "abcdef1234"})
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["continue"])
        self.assertIn("effort=high", payload["systemMessage"])
        self.assertIn("/effort high", payload["systemMessage"])
        context = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Routed effort for this turn: high", context)
        self.assertIn("do not propose switching it", context)
        self.assertIn("reply in the language of the user's prompt", context)
        records = list((self.home / "runs").glob("*-effort-abcdef12/run.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["mode"], "turn_effort")
        self.assertEqual(record["execution_backend"], "same_session")
        self.assertEqual(record["tasks"][0]["execution"]["requested_effort"], "high")
        self.assertIsNone(record["tasks"][0]["execution"]["actual_model"])

    def test_codex_gets_its_own_apply_hint(self):
        sys.argv = ["hook", "codex"]
        _, out, _ = self.invoke({"prompt": "rename one variable"}, choice="low")
        self.assertIn("Alt+.", json.loads(out)["systemMessage"])

    def test_commands_and_empty_prompts_are_left_alone(self):
        sys.argv = ["hook", "claude"]
        for prompt in ("", "   ", "/effort high"):
            code, out, _ = self.invoke({"prompt": prompt})
            self.assertEqual(code, 0)
            self.assertEqual(out, "")

    def test_an_unsupported_host_is_never_routed_as_another_provider(self):
        sys.argv = ["hook", "grok"]
        code, out, _ = self.invoke({"prompt": "trace why the migration drops rows", "session_id": "abc-123"})
        self.assertEqual((code, out), (0, ""))
        self.assertFalse((self.home / "runs").exists())

    def test_a_child_turn_is_never_routed(self):
        sys.argv = ["hook", "claude"]
        os.environ["JEV_ROUTER_CHILD"] = "1"
        try:
            code, out, _ = self.invoke({"prompt": "do the thing"})
        finally:
            os.environ.pop("JEV_ROUTER_CHILD")
        self.assertEqual((code, out), (0, ""))

    def test_behind_the_proxy_the_routed_level_is_handed_to_this_prompt(self):
        sys.argv = ["hook", "claude"]
        self.hook.ensure_proxy = lambda: None  # the proxy process itself is not under test here
        os.environ["ANTHROPIC_BASE_URL"] = self.hook.PROXY_URL
        try:
            _, out, _ = self.invoke({"prompt": "trace why the migration drops rows", "session_id": "abc-123"})
            self.assertEqual((self.home / "effort" / "abc-123").read_text(encoding="utf-8"), "high")
            self.assertIn("applied to this prompt", json.loads(out)["systemMessage"])
            (self.home / ".env").write_text("", encoding="utf-8")  # Jev cannot answer: no stale level survives
            _, out, _ = self.invoke({"prompt": "next turn", "session_id": "abc-123"})
            self.assertFalse((self.home / "effort" / "abc-123").exists())
            payload = json.loads(out)  # no default gear for claude: nothing is routed and no instruction is added
            self.assertIn("your CLI effort setting applies", payload["systemMessage"])
            self.assertNotIn("hookSpecificOutput", payload)
        finally:
            os.environ.pop("ANTHROPIC_BASE_URL")

    def test_a_failed_jev_falls_back_to_an_explicit_default_gear_only(self):
        sys.argv = ["hook", "codex"]
        _, out, _ = self.invoke({"prompt": "rename a variable", "session_id": "s1"}, provider="codex", choice="extreme")
        payload = json.loads(out)
        self.assertIn("effort=medium (your default gear)", payload["systemMessage"])
        self.assertIn("(your default gear)", payload["hookSpecificOutput"]["additionalContext"])
        sys.argv = ["hook", "claude"]
        _, out, _ = self.invoke({"prompt": "rename a variable", "session_id": "s2"}, choice="extreme")
        self.assertNotIn("hookSpecificOutput", json.loads(out))

    def test_without_the_proxy_no_level_file_is_written(self):
        sys.argv = ["hook", "claude"]
        os.environ.pop("ANTHROPIC_BASE_URL", None)
        self.invoke({"prompt": "trace why the migration drops rows", "session_id": "abc-123"})
        self.assertFalse((self.home / "effort").exists())


class ProxyTests(unittest.TestCase):
    """The proxy may only change output_config.effort, and only to a configured level."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("jev_effort_proxy", ROOT / "scripts" / "jev_effort_proxy.py")
        self.proxy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proxy)
        self.temp = tempfile.TemporaryDirectory()
        home = Path(self.temp.name)
        (home / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
        (home / "effort").mkdir()
        (home / "effort" / "sess-1").write_text("high", encoding="utf-8")
        (home / "effort" / "sess-2").write_text("ultra", encoding="utf-8")
        self._env = os.environ.get("JEV_ROUTER_HOME")
        os.environ["JEV_ROUTER_HOME"] = str(home)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("JEV_ROUTER_HOME", None)
        else:
            os.environ["JEV_ROUTER_HOME"] = self._env
        self.temp.cleanup()

    def test_only_configured_levels_for_known_sessions_are_routed(self):
        self.assertEqual(self.proxy.routed_effort("sess-1"), "high")
        self.assertIsNone(self.proxy.routed_effort("sess-2"))  # not in efforts
        self.assertIsNone(self.proxy.routed_effort("missing"))
        self.assertIsNone(self.proxy.routed_effort("../config.json"))

    def test_rewrite_touches_only_an_effort_the_cli_already_sent(self):
        body = json.dumps({"model": "claude-x", "output_config": {"effort": "medium"}, "messages": []}).encode()
        out = json.loads(self.proxy.rewrite(body, "high"))
        self.assertEqual(out, {"model": "claude-x", "output_config": {"effort": "high"}, "messages": []})
        no_effort = json.dumps({"model": "claude-haiku", "messages": []}).encode()
        self.assertEqual(self.proxy.rewrite(no_effort, "high"), no_effort)
        self.assertEqual(self.proxy.rewrite(body, None), body)
        self.assertEqual(self.proxy.rewrite(b"not json", "high"), b"not json")
        codex = json.dumps({"model": "gpt-x", "reasoning": {"effort": "medium", "context": "all_turns"}}).encode()
        self.assertEqual(json.loads(self.proxy.rewrite(codex, "low", "reasoning"))["reasoning"],
                         {"effort": "low", "context": "all_turns"})

    def test_every_model_request_is_logged_with_model_and_the_effort_it_left_with(self):
        log = Path(os.environ["JEV_ROUTER_HOME"]) / "effort" / "applied.log"
        before = json.dumps({"model": "claude-x", "output_config": {"effort": "medium"}}).encode()
        after = self.proxy.rewrite(before, "high")
        self.proxy.note_applied("sess-1-long", "high", before, after, "output_config")
        self.proxy.note_applied("sess-1-long", None, before, before, "output_config")
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual({k: rows[0][k] for k in ("session", "model", "effort", "from", "routed", "changed")},
                         {"session": "sess-1-l", "model": "claude-x", "effort": "high", "from": "medium", "routed": True, "changed": True})
        self.assertEqual((rows[1]["routed"], rows[1]["effort"], rows[1]["changed"]), (False, "medium", False))
        self.assertNotIn("messages", log.read_text(encoding="utf-8"))  # never any prompt content

    def test_a_stream_is_framed_so_the_client_knows_where_the_body_ends(self):
        block = b"data: hi\n\n"
        self.assertEqual(self.proxy.chunk_block(block), b"A\r\n" + block + b"\r\n")
        self.assertEqual(self.proxy.CHUNK_END, b"0\r\n\r\n")

    def test_codex_is_detected_only_when_config_selects_the_jev_provider(self):
        spec = importlib.util.spec_from_file_location("jev_effort_hook", ROOT / "scripts" / "jev_effort_hook.py")
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        home = Path(os.environ["JEV_ROUTER_HOME"])
        saved = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(home)
        try:
            (home / "config.toml").write_text('model = "x"\n[profiles.a]\nmodel_provider = "jev"\n', encoding="utf-8")
            self.assertFalse(hook.proxy_in_use("codex"))  # only the top-level key counts
            (home / "config.toml").write_text('model_provider = "jev"\n[model_providers.jev]\n', encoding="utf-8")
            self.assertTrue(hook.proxy_in_use("codex"))
        finally:
            if saved is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = saved


if __name__ == "__main__":
    unittest.main()
