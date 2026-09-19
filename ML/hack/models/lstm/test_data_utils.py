"""Checks for CSV labels, causal features and train-only preprocessing."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from data_utils import (METRICS, TARGETS, fit_transformer, load_data, prepare_features,
                        select_features, targets, transform_features, completed_inputs)


class DataTests(unittest.TestCase):
    def test_merge_and_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "a.csv").write_text("time_utc,x,y\n2024-01-01T00:00:00Z,1,\n"
                                        "2024-01-01T01:00:00Z,3,4\n")
            (path / "b.csv").write_text("time_utc,x,y\n2024-01-01T00:00:00Z,1,2\n")
            (path / "forecast.csv").write_text("issued_at_utc,text\n2024-01-01T00:00:00Z,hi\n")
            frame, sources = load_data(path)
            self.assertEqual(len(frame), 3)
            self.assertEqual(frame.iloc[0]["y"], 2)
            self.assertTrue(frame.iloc[1].isna().all())
            self.assertEqual(len(sources), 2)
            (path / "b.csv").write_text("time_utc,x\n2024-01-01T00:00:00Z,9\n")
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                load_data(path)

    def test_cutoff_masks_unfinished_indices_before_history_extension(self):
        times = pd.date_range("2024-01-01", periods=6, freq="30min", tz="UTC")
        frame = pd.DataFrame({"geomagnetic_kp": 8.0, "solar_xray_flux_long": 1e-5}, index=times)
        state = fit_transformer(frame, columns=["geomagnetic_kp"])
        past = completed_inputs(frame, times[-1] + pd.Timedelta(minutes=15))
        self.assertTrue(past.geomagnetic_kp.isna().all())
        self.assertTrue(past.solar_xray_flux_long.notna().all())
        encoded = transform_features(past.reindex(times), state)
        self.assertEqual(encoded[-1, 1], 1)
        complete = completed_inputs(frame, times[-1] + pd.Timedelta(minutes=30))
        self.assertTrue(complete.geomagnetic_kp.eq(8).all())

    def test_off_grid_time_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            path.write_text("time_utc,x\n2024-01-01T00:01:00Z,1\n")
            with self.assertRaisesRegex(ValueError, "30-minute grid"):
                load_data(path)

    def test_observed_labels_do_not_require_coverage(self):
        frame = pd.DataFrame(0.1, index=range(6), columns=TARGETS)
        frame.loc[1, METRICS[0] + "_max"] = 10
        frame.loc[2, METRICS[2] + "_max"] = 1
        frame.loc[3, METRICS[2] + "_max"] = -1
        frame.loc[4, METRICS[0] + "_max"] = np.nan
        frame.loc[5, METRICS[0] + "_max"] = np.nan
        frame.loc[5, METRICS[2] + "_max"] = 1
        y, labels = targets(frame)
        np.testing.assert_equal(labels, [0, 1, 1, np.nan, np.nan, 1])
        self.assertTrue(np.isnan(y[3, 5]))
        # These labels describe observed maxima, not inferred sampling coverage.
        frame[METRICS[0] + "_count"] = 1000
        frame[METRICS[0] + "_coverage_fraction"] = 0
        np.testing.assert_equal(targets(frame)[1], labels)

    def test_transform_is_causal_and_expires(self):
        metric = METRICS[0]
        frame = pd.DataFrame({metric: [1, 2, np.nan, np.nan, 20]})
        state = fit_transformer(frame.iloc[:2], max_age=2)
        short = transform_features(frame.iloc[:4], state)
        long = transform_features(frame, state)
        np.testing.assert_array_equal(short, long[:4])
        self.assertEqual(short[2, 0], short[1, 0])
        np.testing.assert_array_equal(short[3], [0, 1, 1])

    def test_all_numeric_features_and_unobserved_channels(self):
        frame = pd.DataFrame({METRICS[0]: [1, 2], "solar_euv_irradiance_304_angstrom": [0.1, 0.2],
                              "solar_mgii_index_standard": [0.27, 0.28],
                              "auroral_electrojet_al": [-100, -200],
                              "differential_protons:channel": [np.nan, np.nan],
                              "differential_protons:channel_count": [0, 0]})
        selected = select_features(frame)
        self.assertEqual(set(selected), set(frame.columns[:4]))
        self.assertEqual(select_features(frame, "core"), [METRICS[0]])
        state = fit_transformer(frame)
        self.assertEqual(state["columns"], selected)
        self.assertTrue(np.isfinite(transform_features(frame, state)).all())

    def test_interval_indices_are_used_after_their_interval(self):
        times = pd.date_range("2026-01-01", periods=60, freq="30min", tz="UTC")
        frame = pd.DataFrame({"geomagnetic_kp": np.arange(60) // 6,
                              "geomagnetic_hp60": np.arange(60) // 2,
                              "geomagnetic_hp30": np.arange(60),
                              "geomagnetic_daily_ap": np.arange(60) // 48,
                              "sunspot_number": np.arange(60) // 48,
                              "solar_radio_flux_f107_observed": np.arange(60) // 48}, index=times)
        result = prepare_features(frame)
        self.assertTrue(result.geomagnetic_kp.iloc[:5].isna().all())
        self.assertEqual(result.geomagnetic_kp.iloc[5], 0)
        self.assertEqual(result.geomagnetic_kp.iloc[10], 0)
        self.assertEqual(result.geomagnetic_kp.iloc[11], 1)
        self.assertTrue(np.isnan(result.geomagnetic_hp60.iloc[0]))
        self.assertEqual(result.geomagnetic_hp60.iloc[2], 0)
        self.assertEqual(result.geomagnetic_hp60.iloc[3], 1)
        np.testing.assert_array_equal(result.geomagnetic_hp30, frame.geomagnetic_hp30)
        for column in ("geomagnetic_daily_ap", "sunspot_number", "solar_radio_flux_f107_observed"):
            self.assertTrue(result[column].iloc[:48].isna().all())
            self.assertEqual(result[column].iloc[48], 0)

    def test_engineered_features_never_read_future_rows(self):
        times = pd.date_range("2026-01-01", periods=80, freq="30min", tz="UTC")
        frame = pd.DataFrame({METRICS[0]: np.arange(80) + 1.0,
                              "solar_xray_flux_long": np.linspace(1e-8, 1e-6, 80),
                              "geomagnetic_kp": np.arange(80) // 6}, index=times)
        state = fit_transformer(frame.iloc[:60], feature_set="engineered", anchors=True)
        self.assertIn(f"derived:{METRICS[0]}:change_6h", state["columns"])
        self.assertIn("derived:time:hour_sin", state["columns"])
        prefix = transform_features(frame.iloc[:60], state)
        changed = frame.copy()
        changed.iloc[60:] = 1e9
        np.testing.assert_array_equal(prefix, transform_features(changed, state)[:60])

    def test_normalization_and_columns_are_fitted_on_training_only(self):
        frame = pd.DataFrame({"solar_mgii_index_standard": [0.2, 0.3, 1000],
                              "differential_protons:late": [np.nan, np.nan, 100],
                              "differential_protons:late_count": [0, 0, 12]})
        state = fit_transformer(frame.iloc[:2])
        self.assertEqual(state["columns"], ["solar_mgii_index_standard"])
        np.testing.assert_allclose(state["mean"], [0.25])
        np.testing.assert_allclose(state["scale"], [0.05])
        np.testing.assert_allclose(transform_features(frame, state)[:2, 0], [-1, 1])

    def test_derived_flux_features_do_not_treat_invalid_values_as_measurements(self):
        frame = pd.DataFrame({METRICS[0]: [-1, 2], "solar_xray_flux_long": [-1, 1e-6]})
        prepared = prepare_features(frame, "engineered")
        proton = prepared[f"derived:{METRICS[0]}:mean_1h"]
        xray = prepared["derived:solar_xray_flux_long:mean_1h"]
        self.assertTrue(np.isnan(proton.iloc[0]))
        self.assertTrue(np.isnan(xray.iloc[0]))
        self.assertAlmostEqual(proton.iloc[1], np.log1p(2))
        self.assertAlmostEqual(xray.iloc[1], -6)

    def test_residual_anchors_use_raw_latest_targets_and_expire(self):
        frame = pd.DataFrame([[1, 2, 3, 4, 5, 6], [np.nan] * 6,
                              [np.nan] * 6, [2, 3, 4, 5, 6, 7]], columns=TARGETS)
        frame[METRICS[0] + "_last"] = 1000
        state = fit_transformer(frame.iloc[:1], max_age=2, anchors=True)
        anchors = transform_features(frame, state)[:, -6:]
        np.testing.assert_allclose(anchors[0], np.log1p([1, 2, 3, 4, 5, 6]))
        np.testing.assert_array_equal(anchors[1], anchors[0])
        np.testing.assert_array_equal(anchors[2], np.zeros(6))
        np.testing.assert_allclose(anchors[3], np.log1p([2, 3, 4, 5, 6, 7]))
        state = fit_transformer(frame.iloc[:1], max_age=1, anchors=True)
        np.testing.assert_array_equal(transform_features(frame, state)[1, -6:], np.zeros(6))

    def test_xray_log_keeps_small_signal(self):
        column = "solar_xray_flux_long"
        frame = pd.DataFrame({column: [1e-8, 1e-7, 1e-6, -1]})
        state = fit_transformer(frame)
        values = transform_features(frame, state)
        self.assertGreater(values[2, 0] - values[0, 0], 2)
        self.assertEqual(values[3, 1], 1)

    def test_small_euv_and_differential_signals_survive_normalization(self):
        frame = pd.DataFrame({"solar_euv_irradiance_304_angstrom": [1e-5, 2e-5, 4e-5],
                              "differential_alphas:channel": [1e-9, 2e-9, 4e-9]})
        state = fit_transformer(frame)
        values = transform_features(frame, state)
        self.assertTrue((np.ptp(values[:, :2], axis=0) > 2).all())


if __name__ == "__main__":
    unittest.main()
