"""SEPNET-inspired BiLSTM + Transformer and latest-day MLP for 32h forecasts."""

import math

import torch
from torch import nn
from torch.nn import functional as F

from models.lstm.model import initialize_residual_head, residual_quantiles

HORIZON = 64


def summarize_history(x, block_steps=48):
    """Summarize observed history in trailing 24h blocks, oldest block first.

    Inputs are finite transformed values, missing masks and ages. Statistics
    include filled values; the mask and age summaries retain data-quality clues.
    A short initial block is retained, including the 6h smoke-test history.
    """
    if x.ndim != 3 or x.shape[1] == 0 or block_steps < 1:
        raise ValueError("Expected nonempty [batch, history, features] and positive block_steps")
    first = (x.shape[1] - 1) % block_steps + 1
    boundaries = [0, *range(first, x.shape[1] + 1, block_steps)]
    summaries = []
    for start, end in zip(boundaries, boundaries[1:]):
        block = x[:, start:end]
        summaries.append(torch.cat((block.amin(1), block.mean(1), block.amax(1)), -1))
    return torch.stack(summaries, dim=1)


class SEPNETForecast(nn.Module):
    def __init__(self, input_size, hidden_size=64, layers=2, residual=False):
        super().__init__()
        if residual and input_size < 6:
            raise ValueError("Residual forecasts need six trailing log1p flux features")
        self.residual = residual
        summary_size, width = input_size * 3, hidden_size * 2
        self.encoder = nn.LSTM(summary_size, hidden_size, layers, batch_first=True,
                               bidirectional=True, dropout=0.1 if layers > 1 else 0)
        layer = nn.TransformerEncoderLayer(width, nhead=4 if width % 4 == 0 else 2,
                                          dim_feedforward=width * 2,
                                          dropout=0.1, batch_first=True)
        self.temporal = nn.TransformerEncoder(layer, num_layers=1, enable_nested_tensor=False)
        latest_size = summary_size + input_size if residual else summary_size
        self.latest = nn.Sequential(nn.Linear(latest_size, hidden_size), nn.GELU(),
                                    nn.Linear(hidden_size, hidden_size), nn.GELU())
        self.fusion = nn.Sequential(nn.Linear(width + hidden_size, hidden_size),
                                    nn.GELU(), nn.Dropout(0.1))
        self.flux = nn.Linear(hidden_size, HORIZON * 3 * 2 * 3)
        self.event = nn.Linear(hidden_size, HORIZON)
        if residual:
            initialize_residual_head(self.flux)
        else:
            nn.init.constant_(self.flux.bias, -2)

    def encode(self, x):
        days = summarize_history(x)
        encoded, _ = self.encoder(days)
        # Bidirectional attention is confined to observed history, never targets.
        position = torch.arange(days.shape[1], device=x.device, dtype=x.dtype)[:, None]
        frequency = torch.exp(torch.arange(0, encoded.shape[-1], 2, device=x.device,
                                           dtype=x.dtype) * (-math.log(10000) / encoded.shape[-1]))
        angles = position * frequency
        positional = torch.stack((angles.sin(), angles.cos()), dim=-1).flatten(1)
        temporal = self.temporal(encoded + positional)[:, -1]
        latest = torch.cat((days[:, -1], x[:, -1]), dim=-1) if self.residual else days[:, -1]
        return self.fusion(torch.cat((temporal, self.latest(latest)), dim=-1))

    def forward(self, x):
        fused = self.encode(x)
        if self.residual:
            return residual_quantiles(self.flux(fused), x[:, -1, -6:]), self.event(fused)
        raw = self.flux(fused).reshape(-1, HORIZON, 3, 2, 3)
        # Ordered nonnegative quantiles in log1p(pfu), with max >= mean.
        mean = F.softplus(raw[..., 0, :]).cumsum(-1)
        maximum = mean + F.softplus(raw[..., 1, :]).cumsum(-1)
        quantiles = torch.stack((mean, maximum), dim=3).flatten(2, 3)
        return quantiles, self.event(fused)
