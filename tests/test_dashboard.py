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
        catalog = self.root / ".codex" / "models_cache.json"
        catalog.parent.mkdir()
        catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-astra", "visibility": "list"}]}), encoding="utf-8")
        claude_catalog = self.root / ".claude" / "cache" / "model-catalog" / "catalog.json"
        claude_catalog.parent.mkdir(parents=True)
        claude_catalog.write_text(json.dumps({"catalog": {"config": {"models": [
            {"id": "claude-sonnet-4-5"}, {"id": "sonnet", "name": "Sonnet alias"}
        ]}}}), encoding="utf-8")
        grok_catalog = self.root / ".grok" / "models_cache.json"
        grok_catalog.parent.mkdir()
        grok_catalog.write_text(json.dumps({"models": [{"id": "grok-4"}]}), encoding="utf-8")

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
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path)
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
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/config") as response:
                visible = json.load(response)
            self.assertEqual(visible["fallback_provider"], "claude")
            self.assertEqual(visible["providers"], ["claude", "codex"])
            self.assertNotIn("secret_setting", visible)

            payload = json.dumps({
                "fallback_provider": "codex",
                "fallback_model": "gpt-6-astra",
                "fallback_effort": "medium",
                "confidence_threshold": 0.3,
            }).encode("utf-8")
            request = urllib.request.Request(
                base + "/api/config", data=payload, method="PUT",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request) as response:
                saved = json.load(response)
            self.assertEqual(saved["fallback_model"], "gpt-6-astra")
            on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["fallback_effort"], "medium")
            self.assertEqual(on_disk["confidence_threshold"], 0.3)
            self.assertEqual(on_disk["secret_setting"], "preserve-me")
            self.assertFalse(list(self.root.glob(".config.json.*.tmp")))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_config_store_rejects_disabled_provider_and_invalid_model(self):
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        with self.assertRaisesRegex(ValueError, "enabled provider"):
            store.update({
                "fallback_provider": "grok", "fallback_model": "grok-model", "fallback_effort": "medium"
            })
        with self.assertRaisesRegex(ValueError, "cannot begin"):
            store.update({
                "fallback_provider": "codex", "fallback_model": "--danger", "fallback_effort": "medium"
            })

    def test_native_fallbacks_are_independent_partial_and_resettable(self):
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        visible = store.public()
        self.assertEqual(visible["native_fallbacks"], {
            "codex": {"model": None}, "claude": {"model": None}, "grok": {"model": None},
        })
        self.assertEqual(set(visible["model_catalog"]), {"codex", "claude", "grok"})
        self.assertEqual(store.update({"native_fallbacks": {"codex": {"model": "gpt-6-astra"}}})
                         ["native_fallbacks"]["codex"]["model"], "gpt-6-astra")
        store.update({"native_fallbacks": {"claude": {"model": "sonnet"}}})
        saved = store.update({"native_fallbacks": {"grok": {"model": "grok-4"}}})
        self.assertEqual(saved["native_fallbacks"], {
            "codex": {"model": "gpt-6-astra"},
            "claude": {"model": "sonnet"},
            "grok": {"model": "grok-4"},
        })
        reset = store.update({"native_fallbacks": {"claude": {"model": ""}, "grok": {"model": None}}})
        self.assertIsNone(reset["native_fallbacks"]["claude"]["model"])
        self.assertIsNone(reset["native_fallbacks"]["grok"]["model"])
        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["fallback_provider"], "claude")
        self.assertEqual(on_disk["fallback_model"], "claude-default")
        self.assertEqual(on_disk["secret_setting"], "preserve-me")

    def test_native_fallback_validation_is_atomic_and_provider_scoped(self):
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        invalid = [
            {"native_fallbacks": {"other": {"model": "gpt-6-astra"}}},
            {"native_fallbacks": {"codex": {"model": "claude-sonnet-4-5"}}},
            {"native_fallbacks": {"claude": {"model": "grok-4"}}},
            {"native_fallbacks": {"grok": {"model": "gpt-6-astra"}}},
            {"native_fallbacks": {"codex": {"model": 1}}},
            {"native_fallbacks": {"codex": {"model": "gpt-6-astra", "extra": True}}},
        ]
        before = self.config_path.read_bytes()
        for payload in invalid:
            with self.assertRaises(ValueError):
                store.update(payload)
            self.assertEqual(self.config_path.read_bytes(), before)

    def test_native_claude_rejects_foreign_legacy_and_polluted_catalog_models(self):
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw["fallback_provider"] = "claude"
        raw["fallback_model"] = "gpt-6-astra"
        self.config_path.write_text(json.dumps(raw), encoding="utf-8")
        claude_catalog = self.root / ".claude" / "cache" / "model-catalog" / "catalog.json"
        claude_catalog.write_text(json.dumps({"catalog": {"config": {"models": [
            {"id": "claude-sonnet-4-5"}, {"id": "sonnet"},
            {"id": "gpt-6-astra"}, {"id": "arbitrary-alias"},
        ]}}}), encoding="utf-8")
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        ids = {item["id"] for item in store.public()["model_catalog"]["claude"]["models"]}
        self.assertIn("claude-sonnet-4-5", ids)
        self.assertIn("sonnet", ids)
        self.assertNotIn("gpt-6-astra", ids)
        self.assertNotIn("arbitrary-alias", ids)
        before = self.config_path.read_bytes()
        for model in ("gpt-6-astra", "arbitrary-alias"):
            with self.assertRaisesRegex(ValueError, "valid claude"):
                store.update({"native_fallbacks": {"claude": {"model": model}}})
            self.assertEqual(self.config_path.read_bytes(), before)

    def test_existing_known_claude_alias_survives_unavailable_catalog(self):
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw["native_fallbacks"] = {"claude": {"model": "opusplan"}}
        self.config_path.write_text(json.dumps(raw), encoding="utf-8")
        (self.root / ".claude" / "cache" / "model-catalog" / "catalog.json").unlink()
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        saved = store.update({"native_fallbacks": {"claude": {"model": "opusplan"}}})
        self.assertEqual(saved["native_fallbacks"]["claude"]["model"], "opusplan")
        with self.assertRaisesRegex(ValueError, "catalog is unavailable"):
            store.update({"native_fallbacks": {"claude": {"model": "sonnet"}}})

    def test_native_fallback_http_put_preserves_legacy_config(self):
        server = dashboard.DashboardServer(
            ("127.0.0.1", 0), dashboard.RunStore(self.root), dashboard.ConfigStore(self.config_path, model_home=self.root)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            payload = json.dumps({"native_fallbacks": {"grok": {"model": "grok-4"}}}).encode("utf-8")
            request = urllib.request.Request(base + "/api/config", data=payload, method="PUT",
                                             headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                saved = json.load(response)
            self.assertEqual(saved["native_fallbacks"]["grok"]["model"], "grok-4")
            self.assertEqual(saved["fallback_model"], "claude-default")
            on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["fallback_effort"], "high")
            self.assertEqual(on_disk["secret_setting"], "preserve-me")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_threshold_bounds_and_catalog_are_validated_without_changing_other_fields(self):
        catalog = self.root / ".codex" / "models_cache.json"
        catalog.parent.mkdir(exist_ok=True)
        catalog.write_text(json.dumps({"models": [
            {"slug": "gpt-test-model", "display_name": "Test", "visibility": "list", "secret": "never expose"},
            {"slug": "hidden-model", "visibility": "hide"}
        ]}), encoding="utf-8")
        store = dashboard.ConfigStore(self.config_path, model_home=self.root)
        visible = store.public()["model_catalog"]
        self.assertEqual([m["id"] for m in visible["codex"]["models"]], ["gpt-test-model"])
        self.assertNotIn("never expose", json.dumps(visible))
        self.assertTrue(any(m["id"] == "claude-default" and m["source"] == "configured"
                            for m in visible["claude"]["models"]))
        patch = {"fallback_provider": "codex", "fallback_model": "gpt-test-model", "fallback_effort": "medium"}
        for value in (0, 0.3, 0.55, 0.9, 1):
            self.assertEqual(store.update(dict(patch, confidence_threshold=value))["confidence_threshold"], value)
        before = self.config_path.read_bytes()
        for value in (-1, 1.01, float("nan"), float("inf"), True, "0.5", None):
            with self.assertRaisesRegex(ValueError, "Confidence threshold"):
                store.update(dict(patch, confidence_threshold=value))
            self.assertEqual(before, self.config_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "catalog"):
            store.update(dict(patch, fallback_model="other-provider-model"))
        catalog.write_text("broken", encoding="utf-8")
        self.assertEqual(store.public()["model_catalog"]["codex"]["models"][0]["id"], "gpt-test-model")

    def test_dashboard_is_always_open_and_polling_does_not_overlap(self):
        markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("aria-expanded", markup)
        self.assertNotIn('class="run-body" hidden', markup)
        self.assertNotIn("setInterval(", script)
        self.assertIn("if (poll.inflight)", script)
        self.assertIn('id="settingsForm"', markup)
        self.assertIn('method: "PUT"', script)
        self.assertIn("function evidenceFlowHtml", script)
        self.assertIn("value > 1 ? value / 100 : value", script)


if __name__ == "__main__":
    unittest.main()
