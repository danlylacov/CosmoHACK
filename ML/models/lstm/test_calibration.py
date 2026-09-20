"""Calibration and holdout validation are distinct; unknowns stay unknown."""

import json
import unittest
from unittest.mock import patch

import numpy as np
import torch

from calibration import calibrate, default_state, intervals, probabilities


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        self.policy = dict(min_calibration_samples=8, min_calibration_events=2,
                           min_test_events=2, min_interval_coverage=0.7,
                           risk_threshold=0.1)

    @staticmethod
    def outputs(n=12):
        target = np.full((n, 64, 6), 1.5)
        q = np.stack((target - 0.5, target, target + 0.5), axis=-1)
        event = np.broadcast_to(np.arange(n)[:, None] % 2, (n, 64)).astype(float).copy()
        logits = event * 2 - 1
        return q, logits, target, event

    def test_default_masks_everything(self):
        q, logits, _, _ = self.outputs()
        state = default_state()
        self.assertTrue(np.isnan(probabilities(logits, state)).all())
        for validated in (False, True):
            self.assertTrue(all(np.isnan(bound).all()
                                for bound in intervals(q, state, validated=validated)))

    def test_two_class_calibration_and_strict_json(self):
        cal, test = self.outputs(), self.outputs()
        state, report = calibrate(cal, test, self.policy, True, 3, 3)
        p = probabilities(test[1], state)
        self.assertTrue(all(group["valid"] and group["scale"] > 0
                            for group in state["probability_groups"]))
        self.assertTrue(np.all((p >= 0) & (p <= 1)))
        self.assertGreater(p[test[3] == 1].min(), p[test[3] == 0].max())
        self.assertTrue(np.asarray(state["interval_valid"]).all())
        json.dumps((state, report), allow_nan=False)

    def test_holdout_rejects_skill_without_hiding_fitted_probability_or_refitting(self):
        cal, test = self.outputs(), list(self.outputs())
        good, _ = calibrate(cal, tuple(test), self.policy, True, 3, 3)
        test[3] = 1 - test[3]
        bad, report = calibrate(cal, tuple(test), self.policy, True, 3, 3)
        for first, second, metric in zip(good["probability_groups"],
                                         bad["probability_groups"], report["bands"]):
            self.assertEqual((first["scale"], first["bias"]), (second["scale"], second["bias"]))
            self.assertFalse(second["valid"])
            self.assertTrue(second["calibrated"])
            self.assertGreater(metric["brier"], metric["baseline_brier"])
        np.testing.assert_array_equal(probabilities(test[1], good), probabilities(test[1], bad))
        self.assertTrue(np.isnan(probabilities(test[1], bad, validated=True)).all())

    def test_constant_prevalence_is_calibrated_but_has_no_skill(self):
        cal, test = list(self.outputs()), list(self.outputs())
        cal[1][:] = 0
        test[1][:] = 0
        state, report = calibrate(tuple(cal), tuple(test), self.policy, True, 3, 3)
        for group, metrics in zip(state["probability_groups"], report["bands"]):
            self.assertTrue(group["calibrated"])
            self.assertFalse(group["valid"])
            self.assertEqual(metrics["brier_skill"], 0)
        np.testing.assert_allclose(probabilities(test[1], state), 0.5)
        self.assertTrue(np.isnan(probabilities(test[1], state, validated=True)).all())

    def test_tiny_positive_skill_cannot_pass_default_or_configured_threshold(self):
        cal, test = self.outputs(), self.outputs()
        for slope, minimum in ((1e-8, 0.01), (0.04, 0.05)):
            policy = dict(self.policy, min_brier_skill=minimum)
            with patch("calibration._platt", return_value=(slope, 0.0)):
                state, report = calibrate(cal, test, policy, True, 3, 3)
            for group, metrics in zip(state["probability_groups"], report["bands"]):
                self.assertGreater(metrics["brier_skill"], 0)
                self.assertLess(metrics["brier_skill"], minimum)
                self.assertTrue(group["calibrated"])
                self.assertFalse(group["valid"])
                self.assertEqual(group["reason"], "brier_not_better_than_prevalence")

    def test_unknown_single_class_and_few_events_are_not_probabilities(self):
        cal, test = self.outputs(), self.outputs()
        for value in (np.nan, 0, 1):
            sparse = list(cal)
            sparse[3] = np.full_like(cal[3], value)
            state, _ = calibrate(tuple(sparse), test, self.policy, True, 3, 3)
            self.assertTrue(np.isnan(probabilities(test[1], state)).all())
        for trained, cal_events, test_events in ((False, 3, 3), (True, 1, 3)):
            state, _ = calibrate(cal, test, self.policy, trained, cal_events, test_events)
            self.assertTrue(np.isnan(probabilities(test[1], state)).all())

    def test_calibration_survives_single_class_or_absent_test(self):
        cal, test = self.outputs(), list(self.outputs())
        test[3][:] = 0
        for outputs in (tuple(test), None):
            state, report = calibrate(cal, outputs, self.policy, True, 3, 0)
            self.assertTrue(all(group["calibrated"] and not group["valid"]
                                for group in state["probability_groups"]))
            self.assertTrue(np.isfinite(probabilities(cal[1], state)).all())
            self.assertTrue(np.isnan(probabilities(cal[1], state, validated=True)).all())
            json.dumps((state, report), allow_nan=False)

    def test_default_minimum_counts_origins_and_allows_one_research_episode(self):
        policy = {"risk_threshold": 0.1}
        cal, test = self.outputs(19), self.outputs(19)
        state, report = calibrate(cal, test, policy, True, 1, 1)
        self.assertTrue(np.asarray(state["interval_valid"]).all())
        self.assertTrue(all(group["calibrated"] for group in state["probability_groups"]))
        self.assertEqual(report["intervals"]["bands"][0]["calibration_origins"], [19] * 6)
        sparse = tuple(value[:2] for value in cal)
        state, report = calibrate(sparse, test, policy, True, 1, 1)
        # 2 origins x 24 horizons still means 2 independent origin scores.
        self.assertFalse(np.asarray(state["interval_calibrated"]).any())
        self.assertFalse(any(group["calibrated"] for group in state["probability_groups"]))
        self.assertEqual(report["intervals"]["bands"][2]["calibration_origins"], [2] * 6)

    def test_offsets_pool_worst_residual_within_band_and_ignore_test_targets(self):
        cal, test = list(self.outputs()), list(self.outputs())
        cal[2][:, 0, 0] = 5  # First band's worst score = 5 - 2 = 3.
        good, _ = calibrate(tuple(cal), tuple(test), self.policy, False, 0, 0)
        offsets = np.asarray(good["interval_offsets"])
        np.testing.assert_array_equal(offsets[:12, 0], 3)
        np.testing.assert_array_equal(offsets[12:, 0], 0)
        test[2][:] = 10
        bad, _ = calibrate(tuple(cal), tuple(test), self.policy, False, 0, 0)
        np.testing.assert_array_equal(good["interval_offsets"], bad["interval_offsets"])
        self.assertTrue(np.asarray(bad["interval_calibrated"]).all())
        self.assertFalse(np.asarray(bad["interval_valid"]).any())
        self.assertTrue(all(np.isfinite(bound).all() for bound in intervals(test[0], bad)))
        self.assertTrue(all(np.isnan(bound).all()
                            for bound in intervals(test[0], bad, validated=True)))

    def test_legacy_state_retains_original_probability_mask(self):
        state = default_state()
        for group in state["probability_groups"]:
            del group["calibrated"]
        state["probability_groups"][0]["valid"] = True
        p = probabilities(self.outputs()[1], state)
        self.assertTrue(np.isfinite(p[:, :12]).all())
        self.assertTrue(np.isnan(p[:, 12:]).all())

    def test_legacy_state_retains_original_interval_mask(self):
        state = default_state()
        del state["interval_calibrated"]
        state["interval_valid"] = (np.indices((64, 6))[0] < 12).tolist()
        for validated in (False, True):
            for bound in intervals(self.outputs()[0], state, validated=validated):
                self.assertTrue(np.isfinite(bound[:, :12]).all())
                self.assertTrue(np.isnan(bound[:, 12:]).all())

    def test_interval_order_statistic_and_missing_target_masks(self):
        cal, test = list(self.outputs()), list(self.outputs())
        cal[2][:] = 3  # Every finite conformal score is exactly 1 log unit.
        test[2][:] = 3
        cal[2][5:, :, 2] = np.nan  # Too few calibration targets.
        test[2][:, :, 4] = np.nan  # No verification targets.
        state, _ = calibrate(tuple(cal), tuple(test), self.policy, False, 0, 0)
        offsets = np.asarray(state["interval_offsets"])
        np.testing.assert_array_equal(offsets[:, [0, 1, 3, 4, 5]], 1)
        for bound in intervals(test[0], state):
            self.assertTrue(np.isnan(bound[..., 2]).all())  # Missing calibration.
            self.assertTrue(np.isfinite(bound[..., 4]).all())  # Experimental: missing test.
        lower, upper = intervals(test[0], state, validated=True)
        self.assertTrue(np.isnan(lower[..., [2, 4]]).all())
        self.assertTrue(np.isnan(upper[..., [2, 4]]).all())
        self.assertTrue(np.isfinite(lower[..., [0, 1, 3, 5]]).all())
        self.assertTrue((lower[..., 0] <= lower[..., 1]).all())
        self.assertTrue((upper[..., 0] <= upper[..., 1]).all())
        self.assertTrue((upper[..., [0, 1, 3, 5]] >= 3).all())


if __name__ == "__main__":
    unittest.main()
