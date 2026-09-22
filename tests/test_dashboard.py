# SPDX-License-Identifier: Apache-2.0
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jev_dashboard", ROOT / "scripts" / "jev_dashboard.py")
dashboard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dashboard)


class DashboardTests(unittest.TestCase):
    def test_lineage_preserves_unknown_models_and_redacts_prompts(self):
        task = {"prompt": "task token=private", "expanded_prompt": "expanded secret=private",
                "execution": {"provider": "codex", "requested_model": "requested-only",
                              "submitted_prompt": "wrapper password=private", "actual_models": [], "session_ids": ["session-1"]}}
        normalized = dashboard.normalize_task(task)
        self.assertEqual(normalized["actual_models"], [])
        self.assertEqual(normalized["model_observation"], "unknown")
        self.assertEqual(normalized["requested_model"], "requested-only")
        for key in ("prompt", "expanded_prompt", "submitted_prompt"):
            self.assertIn("<redacted>", normalized[key])
            self.assertNotIn("private", normalized[key])
        task["execution"]["actual_models"] = ["observed-a", "observed-b"]
        self.assertEqual(dashboard.normalize_task(task)["model_observation"], "multiple")

    def test_legacy_prompt_artifact_is_confined_to_run_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "run"
            run_dir.mkdir()
            outside = root / "prompt.txt"
            outside.write_text("must not read", encoding="utf-8")
            task = {"execution": {"prompt_path": str(outside)}}
            self.assertEqual(dashboard.normalize_task(task, run_dir=run_dir)["submitted_prompt"], "")
            inside = run_dir / "prompt.txt"
            inside.write_text("actual delivered prompt", encoding="utf-8")
            task["execution"]["prompt_path"] = str(inside)
            self.assertEqual(dashboard.normalize_task(task, run_dir=run_dir)["submitted_prompt"], "actual delivered prompt")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        run_dir = self.root / "20260101T000000Z-a1b2c3d4"
        run_dir.mkdir()
        self.run = {
            "run_id": run_dir.name,
            "status": "completed",
            "started_at": "2026-01-01T00:00:00Z",
            "elapsed_seconds": 2.5,
            "tasks": [{
                "id": "demo", "title": "Demo task", "difficulty": "moderate", "category": "reasoning",
                "prompt": "secret prompt", "provider": "claude",
                "route": {"selected_by_jev": "claude_medium", "confidence": 0.8, "effort": "medium", "probabilities": {"claude_medium": 0.8}},
                "execution": {"status": "completed", "actual_model": "claude-test", "elapsed_seconds": 2, "usage": {"input_tokens": 4, "output_tokens": 6}, "result": "token=supersecret result"}
            }]
        }
        (run_dir / "run.json").write_text(json.dumps(self.run), encoding="utf-8")
        self.config_path = self.root / "config.json"
        self.config = {
            "confidence_threshold": 0.55,
            "efforts": ["low", "medium", "high"],
            "fallback_provider": "claude",
            "fallback_model": "claude-default",
            "fallback_effort": "high",
            "secret_setting": "preserve-me",
            "providers": {
                "claude": {"enabled": True, "model": None},
                "codex": {"enabled": True, "model": None},
                "grok": {"enabled": False, "model": None},
            },
        }
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_normalizes_real_schema_and_includes_sanitized_prompt(self):
        item = dashboard.RunStore(self.root).list()[0]
        self.assertEqual(item["tasks"][0]["selected_by_jev"], "claude_medium")
        self.assertEqual(item["tasks"][0]["usage"]["total_tokens"], 10)
        self.assertEqual(item["tasks"][0]["prompt"], "secret prompt")
        self.assertIn("<redacted>", item["tasks"][0]["result_summary"])

    def test_probability_scales_and_task_dependencies_are_normalized(self):
        task = self.run["tasks"][0]
        task["depends_on"] = ["prepare"]
        task["route"]["confidence"] = 73
        task["route"]["probabilities"] = {"codex_medium": 73, "claude_medium": 12, "grok_medium": 2}
        run_file = self.root / self.run["run_id"] / "run.json"
        run_file.write_text(json.dumps(self.run), encoding="utf-8")
        normalized = dashboard.RunStore(self.root).list()[0]["tasks"][0]
        self.assertEqual(normalized["confidence"], 0.73)
        self.assertEqual(normalized["probabilities"]["codex_medium"], 0.73)
        self.assertEqual(normalized["probabilities"]["claude_medium"], 0.12)
        self.assertEqual(normalized["depends_on"], ["prepare"])

    def test_legacy_native_worker_record_is_not_blank(self):
        legacy = self.root / "native-legacy"
        legacy.mkdir()
        (legacy / "run.json").write_text(json.dumps({
            "mode": "native",
            "execution_backend": "native_subagent",
            "origin_provider": "Codex",
            "parent_identity": "/root",
            "user_prompt": "legacy native run",
            "workers": [{
                "task": "strategy_review",
                "provider": "OpenAI/Codex",
                "requested_model": "gpt-5.6-sol",
                "requested_effort": "high",
                "observed_model": None,
                "agent_id": "/root/strategy_review",
                "native_tool": "collaboration.spawn_agent",
                "outcome": "review completed; parent verified",
            }],
        }), encoding="utf-8")
        item = next(i for i in dashboard.RunStore(self.root).list() if i["run_id"] == "native-legacy")
        self.assertEqual(item["status"], "completed")
        self.assertTrue(item["started_at"])
        self.assertEqual(item["parent_agent"], "/root")
        self.assertEqual(item["task_count"], 1)
        task = item["tasks"][0]
        self.assertEqual(task["title"], "strategy_review")
        self.assertEqual(task["requested_model"], "gpt-5.6-sol")
        self.assertEqual(task["effort"], "high")
        self.assertEqual(task["session_ids"], ["/root/strategy_review"])
        self.assertEqual(task["model_evidence_source"], "collaboration.spawn_agent")
        self.assertEqual(task["model_observation"], "unknown")
        self.assertIn("review completed", task["result_summary"])

    def test_malformed_run_is_visible_not_fatal(self):
        bad = self.root / "bad"
        bad.mkdir()
        (bad / "run.json").write_text("{", encoding="utf-8")
        statuses = {item["status"] for item in dashboard.RunStore(self.root).list()}
        self.assertIn("unreadable", statuses)

    def test_cache_reuses_unchanged_record_and_invalidates_on_write(self):
        store = dashboard.RunStore(self.root)
        first = store.list()[0]
        self.assertIs(first, store.list()[0])
        run_file = self.root / self.run["run_id"] / "run.json"
        self.run["status"] = "running"
        run_file.write_text(json.dumps(self.run) + " ", encoding="utf-8")
        os.utime(run_file, None)
        self.assertEqual(store.list()[0]["status"], "running")

    def test_partial_write_uses_last_good_record(self):
        store = dashboard.RunStore(self.root)
        first = store.list()[0]
        run_file = self.root / self.run["run_id"] / "run.json"
        run_file.write_text("{", encoding="utf-8")
        os.utime(run_file, None)
        stale = store.list()[0]
        self.assertEqual(stale["run_id"], first["run_id"])
        self.assertTrue(stale["stale"])

    def test_rejects_path_traversal(self):
        self.assertIsNone(dashboard.RunStore(self.root).get("../outside"))

    def test_http_api_and_read_only_guard(self):
        server = dashboard.DashboardServer(
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path, model_home=self.root)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/runs") as response:
                payload = json.load(response)
                self.assertEqual(payload["total"], 1)
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            request = urllib.request.Request(base + "/api/runs", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code, 405)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_live_api_discovers_a_new_run_without_restart(self):
        server = dashboard.DashboardServer(
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path, model_home=self.root)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/runs") as response:
                self.assertEqual(json.load(response)["total"], 1)
            new_dir = self.root / "20260101T000100Z-e5f6a7b8"
            new_dir.mkdir()
            new_run = dict(self.run, run_id=new_dir.name, status="running")
            (new_dir / "run.json").write_text(json.dumps(new_run), encoding="utf-8")
            with urllib.request.urlopen(base + "/api/runs") as response:
                payload = json.load(response)
                self.assertEqual(payload["total"], 2)
                self.assertEqual(payload["runs"][0]["run_id"], new_dir.name)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_config_api_exposes_only_safe_fields_and_updates_atomically(self):
        server = dashboard.DashboardServer(
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path, model_home=self.root)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/config") as response:
                visible = json.load(response)
            self.assertEqual(set(visible), {"native_fallbacks", "effort_options", "config_path"})
            self.assertEqual(visible["effort_options"], ["low", "medium", "high"])
            self.assertNotIn("secret_setting", visible)

            payload = json.dumps({
                "native_fallbacks": {"codex": {"effort": "high"}},
            }).encode("utf-8")
            request = urllib.request.Request(
                base + "/api/config", data=payload, method="PUT",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                saved = json.load(response)
            self.assertEqual(saved["native_fallbacks"]["codex"]["effort"], "high")
            on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["fallback_effort"], "high")
            self.assertEqual(on_disk["confidence_threshold"], 0.55)
            self.assertEqual(on_disk["secret_setting"], "preserve-me")
            self.assertFalse(list(self.root.glob(".config.json.*.tmp")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_removed_policy_is_rejected_without_writing(self):
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        before = self.config_path.read_bytes()
        for patch in ({"fallback_provider": "codex", "fallback_model": "gpt-6-astra", "fallback_effort": "medium"},
                      {"confidence_threshold": 0.5},
                      {"native_fallbacks": {}, "fallback_model": "gpt-6-astra"}):
            with self.assertRaisesRegex(ValueError, "only native_fallbacks"):
                store.update(patch)
            self.assertEqual(self.config_path.read_bytes(), before)

    def test_native_fallbacks_are_independent_partial_and_resettable(self):
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        visible = store.public()
        self.assertEqual(visible["native_fallbacks"], {
            "codex": {"effort": None}, "claude": {"effort": None}, "grok": {"effort": None},
        })
        self.assertEqual(store.update({"native_fallbacks": {"codex": {"effort": "high"}}})
                         ["native_fallbacks"]["codex"]["effort"], "high")
        store.update({"native_fallbacks": {"claude": {"effort": "low"}}})
        saved = store.update({"native_fallbacks": {"grok": {"effort": "medium"}}})
        self.assertEqual(saved["native_fallbacks"], {
            "codex": {"effort": "high"},
            "claude": {"effort": "low"},
            "grok": {"effort": "medium"},
        })
        reset = store.update({"native_fallbacks": {"claude": {"effort": ""}, "grok": {"effort": None}}})
        self.assertIsNone(reset["native_fallbacks"]["claude"]["effort"])
        self.assertIsNone(reset["native_fallbacks"]["grok"]["effort"])
        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["fallback_provider"], "claude")
        self.assertEqual(on_disk["fallback_model"], "claude-default")
        self.assertEqual(on_disk["secret_setting"], "preserve-me")

    def test_native_fallback_validation_is_atomic_and_provider_scoped(self):
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        invalid = [
            {"native_fallbacks": {"other": {"effort": "high"}}},
            {"native_fallbacks": {"codex": {"effort": "extreme"}}},
            {"native_fallbacks": {"claude": {"effort": "gpt-6-astra"}}},
            {"native_fallbacks": {"codex": {"effort": 1}}},
            {"native_fallbacks": {"codex": {"model": "gpt-6-astra"}}},
            {"native_fallbacks": {"codex": {"effort": "high", "extra": True}}},
        ]
        before = self.config_path.read_bytes()
        for payload in invalid:
            with self.assertRaises(ValueError):
                store.update(payload)
            self.assertEqual(self.config_path.read_bytes(), before)

    def test_native_fallback_http_put_preserves_legacy_config(self):
        server = dashboard.DashboardServer(
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path, model_home=self.root)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            payload = json.dumps({"native_fallbacks": {"grok": {"effort": "low"}}}).encode("utf-8")
            request = urllib.request.Request(base + "/api/config", data=payload, method="PUT",
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                saved = json.load(response)
            self.assertEqual(saved["native_fallbacks"]["grok"]["effort"], "low")
            self.assertNotIn("fallback_model", saved)
            on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["fallback_effort"], "high")
            self.assertEqual(on_disk["secret_setting"], "preserve-me")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_dashboard_is_always_open_and_polling_does_not_overlap(self):
        markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("aria-expanded", markup)
        self.assertNotIn('class="run-body" hidden', markup)
        self.assertNotIn("setInterval(", script)
        self.assertIn("if (poll.inflight)", script)
        self.assertIn('id="nativeSettingsForm"', markup)
        self.assertNotIn("Legacy CLI fallback policy", markup)
        self.assertIn('id="nativeEffort-codex"', markup)
        self.assertNotIn("nativeModel-", markup)
        self.assertIn('method: "PUT"', script)
        self.assertIn("function evidenceFlowHtml", script)
        self.assertIn("value > 1 ? value / 100 : value", script)


if __name__ == "__main__":
    unittest.main()
