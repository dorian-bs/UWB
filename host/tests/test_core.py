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

VALID_LINE_V2 = json.dumps(
    {
        "type": "range",
        "v": 2,
        "board": 1,
        "peer": 2,
        "seq": 8,
        "time_ms": 1300,
        "raw_m": 2.12,
        "corrected_m": 2.10,
        "valid": True,
        "radio_revision": 7,
        "ipatov_power": 123456,
        "ipatov_f1": 1000,
        "ipatov_f2": 2000,
        "ipatov_f3": 3000,
        "ipatov_fp_index": 512,
        "ipatov_accum_count": 128,
        "cia_diag1": 42,
    }
)


class CoreTests(unittest.TestCase):
    def test_parses_firmware_range_message(self) -> None:
        sample = parse_range_line(VALID_LINE)
        self.assertIsNotNone(sample)
        self.assertEqual(sample["seq"], 7)
        self.assertAlmostEqual(sample["corrected_m"], 2.10)

    def test_keeps_optional_ds_twr_intervals_and_rejects_bad_ones(self) -> None:
        message = json.loads(VALID_LINE_V2)
        message.update(ra_dtu=1_000_000, rb_dtu=1_100_000, da_dtu=900_000, db_dtu=950_000)
        sample = parse_range_line(json.dumps(message))
        self.assertEqual(
            [sample[k] for k in ("ra_dtu", "rb_dtu", "da_dtu", "db_dtu")],
            [1_000_000, 1_100_000, 900_000, 950_000],
        )
        self.assertNotIn("ra_dtu", parse_range_line(VALID_LINE_V2))
        message["db_dtu"] = -1
        self.assertIsNone(parse_range_line(json.dumps(message)))

    def test_rejects_debug_and_invalid_messages(self) -> None:
        self.assertIsNone(parse_range_line("ds_twr.distance = 2.1 m"))
        self.assertIsNone(parse_range_line('{"type":"range","v":2}'))

    def test_diag_lines_are_kept_apart_from_samples_and_malformed_counts(self) -> None:
        state = LabState()
        line = '{"type":"diag","v":1,"board":1,"role":"tx","tick_ms":1000,"wall_ms":1800}'
        self.assertIsNone(state.add_line(line))
        self.assertEqual(state.last_diag["wall_ms"], 1800)
        self.assertEqual((state.malformed_lines, state.ignored_lines, state.total_samples), (0, 0, 0))

    def test_keeps_recent_ignored_lines_and_last_sample_time(self) -> None:
        state = LabState()
        for i in range(7):
            state.add_line(f"[IRQ][board 1] timeout, total={i}")
        self.assertEqual(len(state.recent_ignored), 5)
        self.assertTrue(state.recent_ignored[-1].endswith("total=6"))
        self.assertIsNone(state.last_sample_monotonic)
        state.add_line(VALID_LINE)
        self.assertIsNotNone(state.last_sample_monotonic)

    def test_parses_firmware_range_message_v2(self) -> None:
        sample = parse_range_line(VALID_LINE_V2)
        self.assertIsNotNone(sample)
        self.assertEqual(sample["v"], 2)
        self.assertEqual(sample["ipatov_power"], 123456)

    def test_derives_power_diagnostics_without_touching_raw_fields(self) -> None:
        state = LabState()
        sample = state.add_line(VALID_LINE_V2)
        # 10*log10(123456 * 2^21 / 128^2) - 121.7 and 10*log10((1e6+4e6+9e6) / 128^2) - 121.7
        self.assertAlmostEqual(sample["rx_power_dbm"], -49.71, places=1)
        self.assertAlmostEqual(sample["fp_power_dbm"], -92.38, places=1)
        self.assertAlmostEqual(sample["fp_gap_db"], sample["rx_power_dbm"] - sample["fp_power_dbm"])
        self.assertEqual(sample["ipatov_power"], 123456)
        self.assertNotIn("rx_power_dbm", state.add_line(VALID_LINE))

    def test_power_diagnostics_are_none_for_empty_accumulator(self) -> None:
        line = json.loads(VALID_LINE_V2)
        line["ipatov_accum_count"] = 0
        sample = LabState().add_line(json.dumps(line))
        self.assertIsNone(sample["fp_gap_db"])

    def test_rejects_v2_when_diagnostics_missing_or_invalid(self) -> None:
        missing = json.loads(VALID_LINE_V2)
        del missing["ipatov_f1"]
        self.assertIsNone(parse_range_line(json.dumps(missing)))

        wrong_type = json.loads(VALID_LINE_V2)
        wrong_type["ipatov_power"] = -1
        self.assertIsNone(parse_range_line(json.dumps(wrong_type)))

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
