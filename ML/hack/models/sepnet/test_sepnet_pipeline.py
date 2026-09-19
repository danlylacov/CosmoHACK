"""Exercise both public CLIs, historical cutoffs and checkpoint compatibility."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO))

from models.lstm import infer as lstm_infer
from models.lstm import train as pipeline
from models.lstm.data_utils import HORIZON, METRICS, STEP, TARGETS
from models.lstm.model import LSTMForecast
from models.sepnet import infer, train


class SEPNETPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.work = Path(cls.directory.name)
        cls.csv, cls.artifacts = cls.work / "series.csv", cls.work / "artifacts"
        times = pd.date_range("2025-01-01", periods=800, freq=STEP, tz="UTC", name="time_utc")
        values = np.tile([0.2, 0.3, 0.1, 0.2, 0.05, 0.1], (len(times), 1))
        cls.frame = pd.DataFrame(values, index=times, columns=TARGETS)
        cls.frame.iloc[220:228, 1] = 12
        cls.frame["solar_euv_irradiance_304_angstrom"] = np.linspace(1e-5, 3e-5, len(times))
        cls.frame["geomagnetic_kp"] = (np.arange(len(times)) // 6) % 8
        cls.frame["differential_protons:late"] = np.nan
        cls.frame["differential_protons:late_count"] = 0
        cls.frame.loc[times[770]:, "differential_protons:late"] = 0.01
        cls.frame.loc[times[770]:, "differential_protons:late_count"] = 12
        cls.frame.to_csv(cls.csv)
        cls.run_cli("train.py", "--data", cls.csv, "--output", cls.artifacts,
                    "--epochs", "1", "--hidden-size", "4")
        cls.checkpoint = cls.artifacts / "model.pt"
        cls.state = torch.load(cls.checkpoint, map_location="cpu", weights_only=True)

    @staticmethod
    def run_cli(script, *args, cwd=REPO):
        return subprocess.run([sys.executable, str(ROOT / script), *map(str, args)],
                              cwd=cwd, capture_output=True, text=True, check=True, timeout=60)

    def test_normal_training_and_inference_contract(self):
        self.assertEqual(self.state["context"], 144)
        self.assertEqual(self.state["model_type"], "sepnet")
        self.assertFalse(self.state["smoke"])
        report = json.loads((self.artifacts / "training_report.json").read_text())
        self.assertTrue(report["classifier_trained"])
        self.assertEqual(report["training_positive_windows"], 8)
        self.assertGreater(report["training_negative_windows"], 0)
        self.assertTrue(report["residual"])
        self.assertEqual(report["feature_set"], "engineered")
        self.assertIn("solar_euv_irradiance_304_angstrom", report["features"])
        self.assertIn("geomagnetic_kp", report["features"])
        self.assertIn(f"derived:{TARGETS[0]}:change_6h", report["features"])
        self.assertNotIn("differential_protons:late", report["features"])
        self.assertNotIn("differential_protons:late_count", report["features"])
        self.assertEqual(self.state["model_config"]["input_size"], report["feature_count"] * 3 + 6)
        self.assertEqual(len(report["windows"]), 4)
        self.assertIn("sepnet_mae_pfu", report["evaluation"]["regression"]["0-6h"])
        output = self.work / "forecast"
        self.run_cli("infer.py", "--data", self.csv, "--checkpoint", self.checkpoint,
                     "--output", output, cwd=ROOT)
        result = json.loads((output / "forecast.json").read_text())
        self.assertEqual((result["model_type"], result["context_hours"]), ("sepnet", 72))
        self.assertEqual(len(result["windows"]), HORIZON)
        csv = pd.read_csv(output / "forecast.csv")
        self.assertEqual(len(csv), HORIZON)
        self.assertEqual(set(csv.columns), set(result["windows"][0]))
        for row in result["windows"]:
            self.assertEqual(pd.Timestamp(row["end_utc"]) - pd.Timestamp(row["start_utc"]),
                             pd.Timedelta(STEP))
            for metric in METRICS:
                mean, maximum = (row[f"{metric}_{stat}_pred"] for stat in ("mean", "max"))
                self.assertTrue(np.isfinite([mean, maximum]).all())
                self.assertGreaterEqual(maximum, mean)
            self.assertNotIn("confidence", row)
            self.assertNotIn("unvalidated_probability", row["reason"])
            self.assertIn("p_adverse", row)
            self.assertIn("eva_allowed", row)

    def test_zero_event_weight_disables_classifier(self):
        output = self.work / "regression_only"
        self.run_cli("train.py", "--data", self.csv, "--output", output,
                     "--epochs", "1", "--hidden-size", "4", "--event-weight", "0")
        state = torch.load(output / "model.pt", map_location="cpu", weights_only=True)
        self.assertFalse(state["report"]["classifier_trained"])
        self.assertTrue(all(not group["calibrated"] for group in state["calibration"]["probability_groups"]))

    def test_future_values_do_not_change_historical_prediction(self):
        origin = self.frame.index[400]
        expected = infer.forecast(self.checkpoint, self.csv, origin)
        changed = self.frame.copy()
        changed.loc[origin:] *= 1000
        csv = self.work / "changed.csv"
        changed.to_csv(csv)
        actual = infer.forecast(self.checkpoint, csv, origin)
        # Source hashes change, but predictions, intervals and decisions must not.
        for old, new in zip(expected["windows"], actual["windows"]):
            self.assertEqual({k: v for k, v in old.items() if k != "source_refs"},
                             {k: v for k, v in new.items() if k != "source_refs"})

    def test_checkpoint_families_and_legacy_lstm(self):
        with self.assertRaisesRegex(ValueError, "belongs to sepnet"):
            lstm_infer.forecast(self.checkpoint, self.csv)
        legacy = dict(self.state)
        legacy.pop("model_type")
        legacy["state_dict"] = LSTMForecast(**legacy["model_config"]).state_dict()
        checkpoint = self.work / "legacy_lstm.pt"
        torch.save(legacy, checkpoint)
        result = lstm_infer.forecast(checkpoint, self.csv)
        self.assertEqual(result["model_type"], "lstm")
        self.assertEqual(len(result["windows"]), HORIZON)
        self.assertIsNotNone(result["windows"][0][METRICS[0] + "_mean_pred"])
        with self.assertRaisesRegex(ValueError, "belongs to lstm"):
            infer.forecast(checkpoint, self.csv)

    def test_cli_default_paths_and_model_directory_execution(self):
        for script in ("train.py", "infer.py"):
            self.assertIn("SEPNET", self.run_cli(script, "--help", cwd=ROOT).stdout)
        with patch.object(sys, "argv", ["train.py"]), patch.object(pipeline, "train") as call:
            train.main()
        args, _, family, root = call.call_args.args
        self.assertEqual((family, root, args.context_hours), ("sepnet", ROOT, 72))
        self.assertEqual(args.data, pipeline.data_path(ROOT))
        self.assertEqual(args.output, ROOT / "artifacts")
        self.assertEqual(args.policy, ROOT / "policy.json")
        self.assertEqual(args.features, "engineered")
        self.assertEqual(args.learning_rate, 3e-4)
        self.assertFalse(args.no_residual)
        with patch.object(sys, "argv", ["infer.py", "--smoke"]):
            with patch.object(lstm_infer, "forecast", side_effect=RuntimeError("captured")) as call:
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    infer.main()
        self.assertEqual(call.call_args.args[0], ROOT / "artifacts/smoke/model.pt")
        self.assertEqual(call.call_args.kwargs["model_type"], "sepnet")


if __name__ == "__main__":
    unittest.main()
