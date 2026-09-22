# SPDX-License-Identifier: Apache-2.0
"""The effort router must never route the model and never outlive its 1 second cap."""
import importlib.util
import json
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


if __name__ == "__main__":
    unittest.main()
