"""Architecture, historical aggregation and checkpoint compatibility checks."""

import io
from pathlib import Path
import sys
import unittest

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from models.sepnet.model import HORIZON, SEPNETForecast, summarize_history


class ModelTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(7)
        torch.set_num_threads(1)
        self.model = SEPNETForecast(input_size=9, hidden_size=8, layers=1)

    def test_trailing_blocks_are_chronological(self):
        values = torch.arange(7, dtype=torch.float32)
        x = torch.stack((values, values + 10), dim=-1)[None]
        actual = summarize_history(x, block_steps=3)
        expected = torch.tensor([[[0, 10, 0, 10, 0, 10],
                                  [1, 11, 2, 12, 3, 13],
                                  [4, 14, 5, 15, 6, 16]]], dtype=torch.float32)
        torch.testing.assert_close(actual, expected)

    def test_complete_days_and_partial_smoke_day(self):
        self.assertEqual(summarize_history(torch.ones(2, 144, 9)).shape, (2, 3, 27))
        self.assertEqual(summarize_history(torch.ones(2, 12, 9)).shape, (2, 1, 27))
        with self.assertRaises(ValueError):
            summarize_history(torch.ones(2, 0, 9))

    def test_forecast_constraints_for_72h_and_smoke_history(self):
        for history in (144, 12):
            quantiles, logits = self.model(torch.randn(2, history, 9))
            self.assertEqual(tuple(quantiles.shape), (2, HORIZON, 6, 3))
            self.assertEqual(tuple(logits.shape), (2, HORIZON))
            self.assertTrue(torch.isfinite(quantiles).all())
            self.assertTrue(torch.isfinite(logits).all())
            self.assertTrue((quantiles >= 0).all())
            self.assertTrue((quantiles.diff(dim=-1) >= 0).all())
            self.assertTrue((quantiles[:, :, 1::2] >= quantiles[:, :, ::2]).all())

    def test_gradients_reach_both_branches_and_all_days(self):
        x = torch.randn(2, 144, 9, requires_grad=True)
        quantiles, logits = self.model(x)
        (quantiles.mean() + logits.square().mean()).backward()
        for branch in (self.model.encoder, self.model.temporal, self.model.latest,
                       self.model.fusion, self.model.flux, self.model.event):
            gradients = [p.grad for p in branch.parameters()]
            self.assertTrue(all(g is not None and torch.isfinite(g).all() for g in gradients))
            self.assertGreater(sum(g.abs().sum().item() for g in gradients), 0)
        self.assertTrue(torch.isfinite(x.grad).all())
        for day in x.grad.split(48, dim=1):
            self.assertGreater(day.abs().sum().item(), 0)

    def test_save_load_preserves_predictions(self):
        self.model.eval()
        x = torch.randn(1, 144, 9)
        expected = self.model(x)
        buffer = io.BytesIO()
        torch.save(self.model.state_dict(), buffer)
        buffer.seek(0)
        restored = SEPNETForecast(input_size=9, hidden_size=8, layers=1).eval()
        restored.load_state_dict(torch.load(buffer, weights_only=True))
        for actual, wanted in zip(restored(x), expected):
            torch.testing.assert_close(actual, wanted)

    def test_prediction_does_not_depend_on_other_batch_samples(self):
        self.model.eval()
        x = torch.randn(2, 144, 9)
        single = self.model(x[:1])
        for batched, separate in zip(self.model(x), single):
            torch.testing.assert_close(batched[:1], separate)

    def test_residual_initializes_to_latest_observation(self):
        model = SEPNETForecast(input_size=15, hidden_size=8, layers=1, residual=True).eval()
        baseline = torch.tensor([[0, 0.01, 0.2, 0.3, 5, 6], [1, 2, 3, 4, 5, 7.]])
        for history in (144, 12):
            x = torch.randn(2, history, 15)
            x[:, -1, -6:] = baseline
            quantiles, logits = model(x)
            self.assertEqual(tuple(quantiles.shape), (2, HORIZON, 6, 3))
            self.assertEqual(tuple(logits.shape), (2, HORIZON))
            torch.testing.assert_close(quantiles[..., 1], baseline[:, None].expand(-1, HORIZON, -1))
            self.assertTrue((quantiles >= 0).all())
            self.assertTrue((quantiles.diff(dim=-1) >= 0).all())
            self.assertTrue((quantiles[:, :, 1::2] >= quantiles[:, :, ::2]).all())
            self.assertTrue((quantiles[..., 2] - quantiles[..., 0] < 0.05).all())

    def test_residual_gradients_and_latest_inputs_reach_both_branches(self):
        model = SEPNETForecast(input_size=15, hidden_size=8, layers=1, residual=True)
        x = torch.rand(2, 144, 15, requires_grad=True)
        latest_inputs = []
        handle = model.latest.register_forward_pre_hook(lambda module, args: latest_inputs.append(args[0]))
        quantiles, _ = model(x)
        handle.remove()
        torch.testing.assert_close(latest_inputs[0][:, -15:], x[:, -1])
        quantiles.mean().backward()
        for branch in (model.encoder, model.temporal, model.latest, model.fusion, model.flux):
            gradients = [p.grad for p in branch.parameters()]
            self.assertTrue(all(g is not None and torch.isfinite(g).all() for g in gradients))
            self.assertGreater(sum(g.abs().sum().item() for g in gradients), 0)
        for day in x.grad.split(48, dim=1):
            self.assertGreater(day.abs().sum().item(), 0)

    def test_residual_save_load_preserves_predictions(self):
        model = SEPNETForecast(input_size=15, hidden_size=8, layers=1, residual=True).eval()
        restored = SEPNETForecast(input_size=15, hidden_size=8, layers=1, residual=True).eval()
        restored.load_state_dict(model.state_dict(), strict=True)
        x = torch.rand(2, 144, 15)
        for actual, expected in zip(restored(x), model(x)):
            torch.testing.assert_close(actual, expected)


if __name__ == "__main__":
    unittest.main()
