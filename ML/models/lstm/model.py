"""LSTM encoder with a direct, non-autoregressive 32-hour forecast."""

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import Dataset

if __package__:
    from .data_utils import HORIZON
else:
    from data_utils import HORIZON


def initialize_residual_head(head):
    """Start medians at persistence and intervals about 0.02 log1p(pfu) wide."""
    nn.init.normal_(head.weight, std=0.01)
    nn.init.constant_(head.bias, -4)
    with torch.no_grad():
        head.weight[::3].zero_()
        head.bias[::3].zero_()


def residual_quantiles(raw, baseline):
    """Decode median corrections and positive distances to each interval edge."""
    raw = raw.reshape(-1, HORIZON, 6, 3)
    median = (baseline[:, None, :] + raw[..., 0]).clamp_min(0)
    lower = (median - F.softplus(raw[..., 1])).clamp_min(0)
    upper = median + F.softplus(raw[..., 2])
    quantiles = torch.stack((lower, median, upper), dim=-1)
    mean, maximum = quantiles[..., ::2, :], quantiles[..., 1::2, :]
    # Both sequences are ordered; their pointwise maximum remains ordered.
    return torch.stack((mean, torch.maximum(mean, maximum)), dim=-2).flatten(2, 3)


class LSTMForecast(nn.Module):
    def __init__(self, input_size, hidden_size=64, layers=2, residual=False):
        super().__init__()
        if residual and input_size < 6:
            raise ValueError("Residual forecasts need six trailing log1p flux features")
        self.residual = residual
        self.projection = (nn.Sequential(nn.Linear(input_size, hidden_size), nn.GELU())
                           if residual else nn.Identity())
        self.encoder = nn.LSTM(hidden_size if residual else input_size, hidden_size, layers, batch_first=True,
                               dropout=0.1 if layers > 1 else 0)
        self.flux = nn.Linear(hidden_size, HORIZON * 3 * 2 * 3)
        self.event = nn.Linear(hidden_size, HORIZON)
        if residual:
            initialize_residual_head(self.flux)
        else:
            nn.init.constant_(self.flux.bias, -2)

    def forward(self, x):
        _, (hidden, _) = self.encoder(self.projection(x))
        if self.residual:
            return residual_quantiles(self.flux(hidden[-1]), x[:, -1, -6:]), self.event(hidden[-1])
        raw = self.flux(hidden[-1]).reshape(-1, HORIZON, 3, 2, 3)
        # Ordered quantiles in log1p(pfu); max >= mean at every quantile.
        mean = F.softplus(raw[..., 0, :]).cumsum(-1)
        maximum = mean + F.softplus(raw[..., 1, :]).cumsum(-1)
        quantiles = torch.stack((mean, maximum), dim=3).flatten(2, 3)
        return quantiles, self.event(hidden[-1])


class Windows(Dataset):
    """Store one array, not a materialized copy of every overlapping window."""

    def __init__(self, x, y, event, origins, context):
        self.x, self.y, self.event = x, np.log1p(y), event
        self.origins, self.context = list(origins), context

    def __len__(self):
        return len(self.origins)

    def __getitem__(self, index):
        origin = self.origins[index]
        end = min(origin + HORIZON, len(self.y))
        y = np.full((HORIZON, 6), np.nan, dtype=np.float32)
        event = np.full(HORIZON, np.nan, dtype=np.float32)
        y[:end - origin] = self.y[origin:end]
        event[:end - origin] = self.event[origin:end]
        return self.x[origin - self.context:origin], y, event


def loss(pred, logits, target, event, pos_weight=None, event_weight=0.1):
    mask = torch.isfinite(target)
    error = torch.nan_to_num(target)[..., None] - pred
    levels = pred.new_tensor([0.1, 0.5, 0.9])
    pinball = torch.maximum(levels * error, (levels - 1) * error)
    result = (pinball * mask[..., None]).sum() / (3 * mask.sum().clamp_min(1))
    if pos_weight is not None:
        valid = torch.isfinite(event)
        bce = F.binary_cross_entropy_with_logits(
            logits, torch.nan_to_num(event), reduction="none",
            pos_weight=logits.new_tensor(pos_weight))
        result = result + event_weight * (bce * valid).sum() / valid.sum().clamp_min(1)
    return result


@torch.no_grad()
def collect(model, loader):
    model.eval()
    rows = [[], [], [], []]
    for x, target, event in loader:
        quantiles, logits = model(x)
        for output, value in zip(rows, (quantiles, logits, target, event)):
            output.append(value.cpu().numpy())
    return tuple(np.concatenate(row) for row in rows)
