#!/usr/bin/env python3
"""
Датасет строго с двух архивов:

  NCEI GOES-R SEISS
    https://www.ncei.noaa.gov/products/goes-r-space-environment-in-situ
    файлы: data.ngdc.noaa.gov/.../l2/data/{mpsh,sgps}-l2-avg5m/
           data.ngdc.noaa.gov/.../l1b/seis-l1b-{mpsh,sgps,ehis,mpsl}/

  GFZ Kp / Hpo
    https://kp.gfz.de/en/data
    API: https://kp.gfz.de/app/json/

Без SWPC, RTSW, EXIS, MAG. NCEI запаздывает на ~1 сутки.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch_space_weather import (  # noqa: E402
    L1B_EXTRA,
    NCEI_BASE,
    OUTPUT_INTERVAL_MINUTES,
    PRODUCTS,
    fetch_geomagnetic,
    fetch_l1b_product,
    fetch_seiss_product,
    iso_z,
    resample_records,
    utcnow,
)
from to_csv import main as write_csv  # noqa: E402

SOURCES = {
    "ncei": "https://www.ncei.noaa.gov/products/goes-r-space-environment-in-situ",
    "ncei_data": NCEI_BASE,
    "gfz": "https://kp.gfz.de/en/data",
    "gfz_api": "https://kp.gfz.de/app/json/",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NCEI SEISS (L2 5-min + L1b) и GFZ Kp/Hpo. Без SWPC."
    )
    span = parser.add_mutually_exclusive_group()
    span.add_argument("--days", type=float, help="Lookback в сутках (по умолчанию 3).")
    span.add_argument("--hours", type=float, help="Lookback в часах.")
    parser.add_argument(
        "--out",
        type=Path,
        default=HERE / "output",
        help="Каталог для ncei_gfz.json / ncei_gfz.csv",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=HERE / "cache",
        help="Кэш NetCDF",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=OUTPUT_INTERVAL_MINUTES,
        help="Шаг CSV в минутах (по умолчанию 30).",
    )
    parser.add_argument(
        "--mpsl",
        action="store_true",
        help="Также L1b MPS-LO (~220 МБ/сутки).",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Не писать CSV, только JSON.",
    )
    parser.add_argument(
        "--no-ffill",
        action="store_true",
        help="В CSV не протягивать редкие индексы.",
    )
    return parser.parse_args(argv)


def lookback_delta(args: argparse.Namespace) -> timedelta:
    if args.hours is not None:
        if args.hours <= 0:
            raise SystemExit("--hours должен быть > 0")
        return timedelta(hours=args.hours)
    days = 3.0 if args.days is None else args.days
    if days <= 0:
        raise SystemExit("--days должен быть > 0")
    return timedelta(days=days)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    end = utcnow()
    start = end - lookback_delta(args)
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    print(f"Окно: {iso_z(start)} → {iso_z(end)}")
    print("Источники: только NCEI SEISS + GFZ")

    missing_files: list[dict] = []
    geomagnetic = fetch_geomagnetic(start, end)

    listing_cache: dict = {}
    seiss: dict = {}
    for product in PRODUCTS:
        print(f"NCEI L2 avg5m {product}")
        ncei_payload, missing = fetch_seiss_product(
            product, start, end, args.cache, listing_cache
        )
        missing_files.extend(missing)
        if ncei_payload.get("records"):
            ncei_payload = dict(ncei_payload)
            ncei_payload["records_5min"] = ncei_payload["records"]
            ncei_payload["records"] = resample_records(
                ncei_payload["records"], OUTPUT_INTERVAL_MINUTES
            )

        print(f"NCEI L1b {product}")
        l1b_payload, l1b_missing = fetch_l1b_product(
            product, start, end, args.cache, listing_cache
        )
        missing_files.extend(l1b_missing)

        seiss[product] = {
            "satellite": ncei_payload.get("satellite") or l1b_payload.get("satellite"),
            "source": NCEI_BASE,
            "interval_minutes": OUTPUT_INTERVAL_MINUTES,
            "records": [],
            "ncei": ncei_payload,
            "l1b": l1b_payload,
        }

    extra = list(L1B_EXTRA)
    if args.mpsl:
        extra.append("mpsl")
    for extra_product in extra:
        print(f"NCEI L1b {extra_product}")
        payload, missing = fetch_l1b_product(
            extra_product, start, end, args.cache, listing_cache
        )
        missing_files.extend(missing)
        seiss[extra_product] = payload

    result = {
        "fetched_at": iso_z(end),
        "window": {"start": iso_z(start), "end": iso_z(end)},
        "interval_minutes": OUTPUT_INTERVAL_MINUTES,
        "sources": SOURCES,
        "kp": geomagnetic,
        "seiss": seiss,
        "missing_files": missing_files,
    }

    json_path = args.out / "ncei_gfz.json"
    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(
        f"JSON: {json_path} "
        f"(Hp30={len(geomagnetic['Hp30']['datetime'])}, "
        f"Kp={len(geomagnetic['Kp']['datetime'])}, "
        f"mpsh_l2={len(seiss['mpsh']['ncei']['records'])}, "
        f"sgps_l2={len(seiss['sgps']['ncei']['records'])}, "
        f"mpsh_l1b={len(seiss['mpsh']['l1b']['records'])}, "
        f"sgps_l1b={len(seiss['sgps']['l1b']['records'])}, "
        f"ehis_l1b={len(seiss.get('ehis', {}).get('records') or [])}, "
        f"missing={len(missing_files)})"
    )

    if not args.no_csv:
        csv_path = args.out / "ncei_gfz.csv"
        csv_argv = [
            "--input",
            str(json_path),
            "--out",
            str(csv_path),
            "--step",
            str(args.step),
        ]
        if args.no_ffill:
            csv_argv.append("--no-ffill")
        write_csv(csv_argv)

    return 0


if __name__ == "__main__":
    sys.exit(main())
