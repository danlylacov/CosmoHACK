"""Run: python3 models/sepnet/train.py [--smoke]."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[1]))

from models.lstm import train as pipeline
from models.sepnet.model import SEPNETForecast


def main():
    pipeline.main(ROOT, SEPNETForecast, "sepnet")


if __name__ == "__main__":
    main()
