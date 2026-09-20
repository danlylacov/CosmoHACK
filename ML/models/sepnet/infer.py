"""Run: python3 models/sepnet/infer.py [--smoke]."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))

from models.lstm import infer as pipeline
from models.sepnet.model import SEPNETForecast


def forecast(checkpoint_path, data, origin=None, cutoff=None):
    return pipeline.forecast(checkpoint_path, data, origin, cutoff,
                             model_class=SEPNETForecast, model_type="sepnet")


def main():
    pipeline.main(ROOT, SEPNETForecast, "sepnet")


if __name__ == "__main__":
    main()
