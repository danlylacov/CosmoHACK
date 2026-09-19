"""Check forecast cutoffs and separation of chronological training targets."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from calibration import default_state
from data_utils import HORIZON, STEP, TARGETS, fit_transformer
from infer import forecast
from model import LSTMForecast
from train import ROOT, event_count, split_origins


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.csv = Path(self.directory.name) / "series.csv"
        self.checkpoint = Path(self.directory.name) / "model.pt"
        times = pd.date_range("2024-01-01", periods=40, freq=STEP, tz="UTC", name="time_utc")
        self.frame = pd.DataFrame(np.arange(40 * 6).reshape(40, 6) / 100 + 0.1,
                                  index=times, columns=TARGETS)
        self.origin = times[24]
        transformer = fit_transformer(self.frame.iloc[:12])
        config = {"input_size": len(transformer["columns"]) * 3, "hidden_size": 4, "layers": 1}
        torch.manual_seed(11)
        self.state = {"format_version": 1, "model_config": config,
                      "state_dict": LSTMForecast(**config).state_dict(),
                      "transformer": transformer, "context": 12,
                      "calibration": default_state(), "support": np.ones((64, 6)).tolist(),
                      "policy": json.loads((ROOT / "policy.json").read_text()), "smoke": False,
                      "training_data_end_utc": "2023-12-31T00:00:00Z",
                      "sources": [], "code_sha256": "test", "versions": {}}
        torch.save(self.state, self.checkpoint)

    def predict(self, frame, cutoff=None):
        frame.to_csv(self.csv)
        return forecast(self.checkpoint, self.csv, self.origin, cutoff)

    def numbers(self, result):
        return [[value for key, value in row.items() if key.endswith("_pred")]
                for row in result["windows"]]

    def test_appending_or_changing_future_cannot_change_forecast(self):
        expected = self.predict(self.frame.iloc[:24])
        self.assertTrue(np.isfinite(self.numbers(expected)).all())
        future = self.frame.copy()
        future.iloc[24:] *= 1000
        actual = self.predict(future)
        np.testing.assert_array_equal(self.numbers(actual), self.numbers(expected))
        self.assertEqual(actual["last_observed_window_end_utc"], self.origin.isoformat())

    def test_cutoff_excludes_unfinished_aggregate(self):
        cutoff = self.origin - pd.Timedelta(minutes=15)
        expected = self.predict(self.frame.iloc[:23], cutoff)
        changed = self.frame.copy()
        changed.iloc[23:] *= 1000
        actual = self.predict(changed, cutoff)
        self.assertTrue(np.isfinite(self.numbers(actual)).all())
        np.testing.assert_array_equal(self.numbers(actual), self.numbers(expected))
        self.assertEqual(actual["last_observed_window_end_utc"],
                         (self.origin - pd.Timedelta(STEP)).isoformat())

    def test_checkpoint_date_does_not_restrict_inference(self):
        expected = self.predict(self.frame)
        self.state["training_data_end_utc"] = (self.origin + pd.Timedelta(STEP)).isoformat()
        torch.save(self.state, self.checkpoint)
        result = self.predict(self.frame)
        self.assertEqual(len(result["windows"]), HORIZON)
        self.assertTrue(np.isfinite(self.numbers(result)).all())
        np.testing.assert_array_equal(self.numbers(result), self.numbers(expected))
        self.assertEqual(result["windows"], expected["windows"])

    def test_splits_keep_targets_and_crossing_events_separate(self):
        y = np.zeros((1280, 6), dtype=np.float32)
        for boundary in (896, 1024, 1152):
            y[boundary - 10:boundary + 10, 1] = 12
        groups, boundaries = split_origins(y, context=12)
        self.assertEqual(boundaries, [886, 1014, 1142])
        bounds = [12] + boundaries + [len(y)]
        occupied = []
        for origins, start, end in zip(groups, bounds[:-1], bounds[1:]):
            self.assertGreaterEqual(origins.min(), start)
            self.assertLessEqual(origins.max() + HORIZON, end)
            rows = set((origins[:, None] + np.arange(HORIZON)).ravel())
            self.assertTrue(all(rows.isdisjoint(previous) for previous in occupied))
            occupied.append(rows)
        for index, boundary in enumerate(boundaries):
            event = set(range(boundary, boundary + 20))
            self.assertTrue(event.isdisjoint(occupied[index]))
            self.assertTrue(event.issubset(occupied[index + 1]))

    def test_probabilities_require_each_head_to_have_training_labels(self):
        self.state["calibration"]["interval_valid"] = np.ones((64, 6), dtype=bool).tolist()
        for group in self.state["calibration"]["probability_groups"]:
            group.update(valid=True, scale=0, bias=-10)
        support = np.ones((64, 2), dtype=int)
        support[0, 1] = 0
        self.state["event_support"] = support.tolist()
        torch.save(self.state, self.checkpoint)
        result = self.predict(self.frame)
        self.assertIsNone(result["windows"][0]["p_adverse"])
        self.assertIsNone(result["windows"][0]["eva_allowed"])
        self.assertLess(result["windows"][1]["p_adverse"], 0.1)
        self.assertTrue(result["windows"][1]["eva_allowed"])

    def test_calibrated_probability_uses_threshold_without_skill_gate(self):
        self.state["calibration"]["interval_valid"] = np.ones((64, 6), dtype=bool).tolist()
        self.state["event_support"] = np.ones((64, 2), dtype=int).tolist()
        for bias, allowed in ((-3, True), (3, False)):
            for group in self.state["calibration"]["probability_groups"]:
                group.update(calibrated=True, valid=False, scale=0, bias=bias)
            torch.save(self.state, self.checkpoint)
            result = self.predict(self.frame)
            for row in result["windows"]:
                self.assertAlmostEqual(row["p_adverse"], 1 / (1 + np.exp(-bias)))
                self.assertNotIn("confidence", row)
                self.assertNotIn("unvalidated_probability", row["reason"])
                self.assertIs(row["eva_allowed"], allowed)
                self.assertEqual(row["reason"], ["probability_below_policy_threshold" if allowed
                                                else "probability_at_or_above_policy_threshold"])

    def test_experimental_intervals_do_not_enable_decisions(self):
        self.state["calibration"]["interval_calibrated"] = np.ones((64, 6), dtype=bool).tolist()
        for group in self.state["calibration"]["probability_groups"]:
            group.update(calibrated=True, valid=True, scale=0, bias=-3)
        self.state["event_support"] = np.ones((64, 2), dtype=int).tolist()
        torch.save(self.state, self.checkpoint)
        result = self.predict(self.frame)
        for row in result["windows"]:
            self.assertIsNotNone(row["p_adverse"])
            bounds = [value for key, value in row.items() if key.endswith(("_lower", "_upper"))]
            self.assertTrue(np.isfinite(bounds).all())
            self.assertNotIn("confidence", row)
            self.assertIn("unvalidated_intervals", row["reason"])
            self.assertIsNone(row["eva_allowed"])

    def test_missing_recent_proton_data_blocks_forecast(self):
        frame = self.frame.copy()
        frame.iloc[18:24] = np.nan
        result = self.predict(frame)
        for row in result["windows"]:
            self.assertIn("stale_critical_data", row["reason"])
            self.assertIsNone(row["p_adverse"])
            self.assertIsNone(row["eva_allowed"])
            self.assertTrue(all(value is None for key, value in row.items() if key.endswith("_pred")))

    def test_unknown_gaps_do_not_create_independent_events(self):
        self.assertEqual(event_count([1, np.nan, 1, 0, 1]), 2)
        self.assertEqual(event_count([np.nan, np.nan]), 0)


if __name__ == "__main__":
    unittest.main()
