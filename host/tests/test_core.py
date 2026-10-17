import json
import tempfile
import unittest
from pathlib import Path

from uwb_lab.core import LabState, SessionRecorder, make_prior, parse_range_line

VALID_LINE = json.dumps(
    {
        "type": "range",
        "v": 1,
        "board": 1,
        "peer": 2,
        "seq": 7,
        "time_ms": 1200,
        "raw_m": 2.12,
        "corrected_m": 2.10,
        "valid": True,
    }
)


class CoreTests(unittest.TestCase):
    def test_parses_firmware_range_message(self) -> None:
        sample = parse_range_line(VALID_LINE)
        self.assertIsNotNone(sample)
        self.assertEqual(sample["seq"], 7)
        self.assertAlmostEqual(sample["corrected_m"], 2.10)

    def test_rejects_debug_and_invalid_messages(self) -> None:
        self.assertIsNone(parse_range_line("ds_twr.distance = 2.1 m"))
        self.assertIsNone(parse_range_line('{"type":"range","v":2}'))

    def test_position_prior_and_live_residual(self) -> None:
        state = LabState()
        prior = state.set_prior(
            {
                "mode": "positions",
                "node_a_m": [0, 0, 0],
                "node_b_m": [2, 0, 0],
                "uncertainty_m": 0.01,
            }
        )
        sample = state.add_line(VALID_LINE)
        self.assertAlmostEqual(prior.expected_distance_m, 2.0)
        self.assertAlmostEqual(sample["residual_m"], 0.1)
        self.assertEqual(sample["prior_revision"], 1)

    def test_prior_validation(self) -> None:
        with self.assertRaises(ValueError):
            make_prior({"mode": "distance", "distance_m": -1}, 1)

    def test_recording_writes_replayable_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = SessionRecorder(Path(directory))
            session = recorder.start("LOS test", "demo")
            recorder.record_sample(parse_range_line(VALID_LINE))
            recorder.record_sample(parse_range_line(VALID_LINE))
            recorder.stop()
            manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["completed"])
            self.assertEqual(manifest["sample_count"], 2)
            self.assertEqual(len((session / "telemetry.ndjson").read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()


def test_incomplete_run_and_unique_recording_destinations(tmp_path):
    recorder = SessionRecorder(tmp_path)
    parent = tmp_path / "experiments" / "batch-a" / "runs"
    first = recorder.start("run", "demo", parent=parent)
    recorder.stop(completed=False, reason="Acquisition disconnected")
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["completed"] is False
    assert manifest["reason"] == "Acquisition disconnected"
    second = recorder.start("run", "demo", parent=parent)
    assert second != first
    recorder.stop()


def test_failed_recording_start_closes_partial_files(tmp_path, monkeypatch):
    original = Path.open
    opened = []

    def fail_prior(path, *args, **kwargs):
        if path.name == "priors.ndjson":
            raise OSError("disk unavailable")
        stream = original(path, *args, **kwargs)
        opened.append(stream)
        return stream

    recorder = SessionRecorder(tmp_path)
    monkeypatch.setattr(Path, "open", fail_prior)
    import pytest

    with pytest.raises(OSError, match="disk unavailable"):
        recorder.start("run", "demo")
    assert not recorder.active
    assert all(stream.closed for stream in opened)
