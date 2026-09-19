"""SEPNET forecasts for six proton statistics, two XRS statistics and Kp."""

import torch
from torch import nn
from torch.nn import functional as F

from models.lstm.model import initialize_residual_head
from models.sepnet.model import HORIZON, SEPNETForecast


def align_kp(quantiles, phase):
    """Average forecast quantiles within fixed UTC three-hour Kp intervals.

    Phase is the forecast origin's half-hour slot modulo six. Both partial
    edge intervals are retained. This operation uses predictions only.
    """
    if torch.any((phase < 0) | (phase > 5) | (phase != phase.round())):
        raise ValueError("UTC origin phase must be an integer from 0 to 5")
    blocks = (torch.arange(HORIZON, device=quantiles.device)[None] + phase[:, None].long()) // 6
    membership = F.one_hot(blocks, num_classes=12).to(quantiles.dtype)
    totals = membership.transpose(1, 2) @ quantiles
    counts = membership.sum(1).clamp_min(1)
    return membership @ (totals / counts[..., None])


class SEPNETSGR(SEPNETForecast):
    """Residual medians; inputs end in UTC phase followed by nine anchors.

    Anchors: log1p(pfu) for proton mean/max at >10, >50 and >100 MeV;
    log1p(W/m² / 1e-5) for XRS long mean/max; Kp / 3 for the last target.
    """

    def __init__(self, input_size, hidden_size=64, layers=2):
        if input_size < 10:
            raise ValueError("SGR forecasts need UTC phase and nine trailing target anchors")
        super().__init__(input_size, hidden_size, layers, residual=True)
        self.flux = nn.Linear(hidden_size, HORIZON * 9 * 3)
        initialize_residual_head(self.flux)

    def forward(self, x):
        fused = self.encode(x)
        raw = self.flux(fused).reshape(-1, HORIZON, 9, 3)
        median = (x[:, -1, -9:][:, None] + raw[..., 0]).clamp_min(0)
        lower = (median - F.softplus(raw[..., 1])).clamp_min(0)
        upper = median + F.softplus(raw[..., 2])
        quantiles = torch.stack((lower, median, upper), dim=-1)
        mean, maximum = quantiles[..., :8:2, :], quantiles[..., 1:8:2, :]
        flux = torch.stack((mean, torch.maximum(mean, maximum)), dim=-2).flatten(2, 3)
        kp = align_kp(quantiles[..., 8, :].clamp_max(3), x[:, -1, -10])
        return torch.cat((flux, kp[..., None, :]), dim=-2), self.event(fused)
