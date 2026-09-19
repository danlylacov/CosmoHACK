"""Architecture and missing-target checks for the actual training model."""
import unittest

import numpy as np
import torch

from data_utils import HORIZON
from model import LSTMForecast, Windows, loss, residual_quantiles


class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(7)
        torch.set_num_threads(1)
        self.model = LSTMForecast(input_size=9, hidden_size=8, layers=1)

    def test_legacy_state_loads_without_projection_parameters(self):
        restored = LSTMForecast(input_size=9, hidden_size=8, layers=1, residual=False)
        restored.load_state_dict(self.model.state_dict(), strict=True)
        self.assertEqual(restored.encoder.input_size, 9)
        self.assertFalse(any(key.startswith("projection.") for key in restored.state_dict()))
        x = torch.randn(2, 12, 9)
        for actual, expected in zip(restored(x), self.model(x)):
            torch.testing.assert_close(actual, expected)

    def test_residual_starts_at_persistence_with_compact_encoder(self):
        model = LSTMForecast(input_size=1009, hidden_size=8, layers=1, residual=True)
        x = torch.randn(2, 144, 1009)
        baseline = torch.tensor([[0, 0.01, 0.2, 0.3, 5, 6], [1, 2, 3, 4, 5, 7.]])
        x[:, -1, -6:] = baseline
        quantiles, logits = model(x)
        self.assertEqual(model.encoder.input_size, 8)
        self.assertEqual(tuple(quantiles.shape), (2, HORIZON, 6, 3))
        self.assertEqual(tuple(logits.shape), (2, HORIZON))
        torch.testing.assert_close(quantiles[..., 1], baseline[:, None].expand(-1, HORIZON, -1))
        self.assertTrue((quantiles[..., 2] - quantiles[..., 0] < 0.05).all())
        quantiles.mean().backward()
        for branch in (model.projection, model.encoder, model.flux):
            grads = [p.grad for p in branch.parameters()]
            self.assertTrue(all(g is not None and torch.isfinite(g).all() for g in grads))
            self.assertGreater(sum(g.abs().sum().item() for g in grads), 0)

    def test_residual_constraints_after_arbitrary_corrections(self):
        raw = 10 * torch.randn(2, HORIZON, 6, 3)
        quantiles = residual_quantiles(raw, torch.rand(2, 6))
        self.assertTrue(torch.isfinite(quantiles).all())
        self.assertTrue((quantiles >= 0).all())
        self.assertTrue((quantiles.diff(dim=-1) >= 0).all())
        self.assertTrue((quantiles[:, :, 1::2] >= quantiles[:, :, ::2]).all())

    def test_event_weight_scales_only_classification_loss(self):
        quantiles, logits = self.model(torch.randn(2, 12, 9))
        target = torch.ones(2, HORIZON, 6)
        events = torch.zeros(2, HORIZON)
        regression = loss(quantiles, logits, target, events)
        full = loss(quantiles, logits, target, events, pos_weight=2, event_weight=1)
        default = loss(quantiles, logits, target, events, pos_weight=2)
        torch.testing.assert_close(default - regression, (full - regression) * 0.1)

    def test_forecast_constraints(self):
        quantiles, logits = self.model(torch.randn(2, 12, 9))
        self.assertEqual(tuple(quantiles.shape), (2, HORIZON, 6, 3))
        self.assertEqual(tuple(logits.shape), (2, HORIZON))
        self.assertTrue(torch.isfinite(quantiles).all())
        self.assertTrue((quantiles >= 0).all())
        self.assertTrue((quantiles.diff(dim=-1) >= 0).all())
        self.assertTrue((quantiles[:, :, 1::2] >= quantiles[:, :, ::2]).all())

    def test_missing_future_does_not_contribute_to_loss(self):
        quantiles, logits = self.model(torch.randn(2, 12, 9))
        target = torch.full((2, HORIZON, 6), float("nan"))
        target[:, :5] = 1
        events = torch.full((2, HORIZON), float("nan"))
        events[0, :5], events[1, :5] = 1, 0
        expected = loss(quantiles, logits, target, events, pos_weight=2)
        changed, changed_logits = quantiles.clone(), logits.clone()
        changed[:, 5:] += 1000
        changed_logits[:, 5:] -= 1000
        actual = loss(changed, changed_logits, target, events, pos_weight=2)
        torch.testing.assert_close(actual, expected)
        expected.backward()
        for parameter in self.model.parameters():
            self.assertIsNotNone(parameter.grad)
            self.assertTrue(torch.isfinite(parameter.grad).all())

    def test_fully_missing_targets_have_zero_loss(self):
        quantiles, logits = self.model(torch.randn(1, 12, 9))
        target = torch.full((1, HORIZON, 6), float("nan"))
        events = torch.full((1, HORIZON), float("nan"))
        value = loss(quantiles, logits, target, events, pos_weight=1)
        self.assertEqual(value.item(), 0)
        value.backward()
        self.assertTrue(all(torch.isfinite(p.grad).all() for p in self.model.parameters()))

    def test_smoke_windows_keep_short_future_masked(self):
        x = np.arange(48 * 9, dtype=np.float32).reshape(48, 9)
        y = np.arange(48 * 6, dtype=np.float32).reshape(48, 6)
        events = np.zeros(48, dtype=np.float32)
        windows = Windows(x, y, events, origins=[12, 47], context=12)
        history, target, labels = windows[0]
        np.testing.assert_array_equal(history, x[:12])
        self.assertEqual(target.shape, (HORIZON, 6))
        np.testing.assert_allclose(target[:36], np.log1p(y[12:]))
        self.assertTrue(np.isnan(target[36:]).all())
        self.assertTrue(np.isnan(labels[36:]).all())
        _, tail, tail_labels = windows[1]
        self.assertEqual(int(np.isfinite(tail).sum()), 6)
        self.assertEqual(int(np.isfinite(tail_labels).sum()), 1)


if __name__ == "__main__":
    unittest.main()
