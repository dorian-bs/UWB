"""End-to-end smoke test of the dashboard service. Runs on the demo source, no hardware."""

import json
import os
import tempfile
import unittest
from pathlib import Path

# The app reads its configuration at import time, so set it before importing.
_DATA_DIR = Path(tempfile.mkdtemp(prefix="uwb-test-sessions-"))
os.environ["UWB_SOURCE"] = "demo"
os.environ["UWB_DATA_DIR"] = str(_DATA_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from uwb_lab.app import app  # noqa: E402


class AppSmokeTests(unittest.TestCase):
    def test_serves_ui_and_status(self) -> None:
        with TestClient(app) as client:
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            self.assertIn("text/html", page.headers["content-type"])

            status = client.get("/api/status").json()
            self.assertEqual(status["source"], "demo")
            self.assertIn("recording", status)

    def test_prior_updates_and_rejects_bad_input(self) -> None:
        with TestClient(app) as client:
            prior = client.post(
                "/api/prior",
                json={"mode": "positions", "node_a_m": [0, 0, 0], "node_b_m": [3, 4, 0]},
            ).json()
            self.assertAlmostEqual(prior["expected_distance_m"], 5.0)

            bad = client.post("/api/prior", json={"mode": "nonsense"})
            self.assertEqual(bad.status_code, 400)

    def test_recording_round_trip_writes_a_session(self) -> None:
        with TestClient(app) as client:
            started = client.post("/api/recording/start", json={"name": "smoke test"}).json()
            self.assertTrue(started["recording"])

            # The demo source emits every 200 ms; the websocket must deliver a live sample.
            with client.websocket_connect("/ws") as socket:
                snapshot = socket.receive_json()
                self.assertEqual(snapshot["type"], "snapshot")
                message = socket.receive_json()
                self.assertEqual(message["type"], "sample")
                self.assertIn("raw_m", message["sample"])

            stopped = client.post("/api/recording/stop").json()
            self.assertFalse(stopped["recording"])

            session = _DATA_DIR / stopped["session"]
            manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["completed"])
            self.assertGreater(manifest["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
