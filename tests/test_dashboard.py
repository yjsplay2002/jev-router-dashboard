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

    def tearDown(self):
        self.temp.cleanup()

    def test_normalizes_real_schema_and_omits_prompt(self):
        item = dashboard.RunStore(self.root).list()[0]
        self.assertEqual(item["tasks"][0]["selected_by_jev"], "claude_medium")
        self.assertEqual(item["tasks"][0]["usage"]["total_tokens"], 10)
        self.assertNotIn("prompt", item["tasks"][0])
        self.assertIn("<redacted>", item["tasks"][0]["result_summary"])

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
        server = dashboard.DashboardServer(("127.0.0.1", 0), dashboard.RunStore(self.root))
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
        server = dashboard.DashboardServer(("127.0.0.1", 0), dashboard.RunStore(self.root))
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

    def test_dashboard_is_always_open_and_polling_does_not_overlap(self):
        markup = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("aria-expanded", markup)
        self.assertNotIn('class="run-body" hidden', markup)
        self.assertNotIn("setInterval(", script)
        self.assertIn("if (poll.inflight)", script)


if __name__ == "__main__":
    unittest.main()
