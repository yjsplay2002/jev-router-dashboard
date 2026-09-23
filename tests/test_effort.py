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
          "native_fallbacks": {"codex": {"effort": "medium"}, "claude": {"effort": None}, "grok": {"effort": None}}}
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

    def test_unset_provider_effort_uses_the_middle_level_and_bad_choices_are_ignored(self):
        result = self.run_with(lambda *a, **k: {"answers": {"effort": {"choice": "extreme"}}}, provider="claude")
        self.assertFalse(result["routed"])
        self.assertEqual(result["effort"], "medium")
        self.assertEqual(result["reason"], "jev returned no usable effort")

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

    def tearDown(self):
        if self._env is None:
            os.environ.pop("JEV_ROUTER_HOME", None)
        else:
            os.environ["JEV_ROUTER_HOME"] = self._env
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

    def test_a_child_turn_is_never_routed(self):
        sys.argv = ["hook", "claude"]
        os.environ["JEV_ROUTER_CHILD"] = "1"
        try:
            code, out, _ = self.invoke({"prompt": "do the thing"})
        finally:
            os.environ.pop("JEV_ROUTER_CHILD")
        self.assertEqual((code, out), (0, ""))

if __name__ == "__main__":
    unittest.main()
