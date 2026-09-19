#!/usr/bin/env python3
"""Сервис: моки weather + distances, затем ranked EVA-окна."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from merge_time_sections import fetch_interval, rank_eva_windows

DEFAULT_START = "2026-09-18T10:00:00Z"
DEFAULT_END = "2026-09-18T12:00:00Z"
DEFAULT_CRITICAL_KM = 1.0


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    start = args[0] if len(args) > 0 else DEFAULT_START
    end = args[1] if len(args) > 1 else DEFAULT_END
    bundle = fetch_interval(
        start,
        end,
        use_mock=True,
        critical_distance_km=DEFAULT_CRITICAL_KM,
    )
    ranked = rank_eva_windows(bundle)
    bundle["windows"] = ranked["windows"]
    json.dump(bundle, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
