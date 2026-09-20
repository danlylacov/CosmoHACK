"""Regression checks: helper goldens and mocked endpoint payloads must stay stable."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distances import (  # noqa: E402
    build_critical_intervals,
    expand_window_with_minutes,
)
from get_data import parse_socrates_html  # noqa: E402
from main import app  # noqa: E402
from payload import data_quality_status, vec3  # noqa: E402
from responses import HAS_ORJSON, WindowError  # noqa: E402
from satcat import classify_norad, is_iss_associated_record, satcat_field  # noqa: E402
from timeutil import (  # noqa: E402
    iso_utc,
    merge_intervals,
    minute_grid,
    parse_request_window,
    parse_tca,
    seconds_in_intervals,
    union_duration_seconds,
)

UTC = timezone.utc


class TimeutilGoldens(unittest.TestCase):
    def test_parse_tca_formats(self):
        cases = {
            "": None,
            "  ": None,
            "not-a-date": None,
            "2026-09-19 12:00:00.123": "2026-09-19T12:00:00.123000+00:00",
            "2026-09-19 12:00:00": "2026-09-19T12:00:00+00:00",
            "2026-09-19T12:00:00.123Z": "2026-09-19T12:00:00.123000+00:00",
            "2026-09-19T12:00:00Z": "2026-09-19T12:00:00+00:00",
            "2026-09-19T12:00:00+00:00": "2026-09-19T12:00:00+00:00",
            "2026-09-19T12:00:00.5+00:00": "2026-09-19T12:00:00.500000+00:00",
            "2026-09-19T12:00:00+05:00": "2026-09-19T07:00:00+00:00",
        }
        for raw, expected in cases.items():
            parsed = parse_tca(raw)
            got = parsed.isoformat() if parsed else None
            self.assertEqual(got, expected, raw)

    def test_iso_utc_drops_microseconds_and_converts_offset(self):
        self.assertEqual(
            iso_utc(datetime(2026, 9, 19, 12, 0, 0, 123456, tzinfo=UTC)),
            "2026-09-19T12:00:00Z",
        )
        self.assertEqual(
            iso_utc(datetime(2026, 9, 19, 15, 0, 0, tzinfo=timezone(timedelta(hours=3)))),
            "2026-09-19T12:00:00Z",
        )

    def test_merge_and_union_intervals(self):
        a = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        ivs = [
            (a, a + timedelta(seconds=10)),
            (a + timedelta(seconds=5), a + timedelta(seconds=12)),
            (a + timedelta(seconds=20), a + timedelta(seconds=25)),
            (a + timedelta(seconds=25), a + timedelta(seconds=30)),
        ]
        merged = merge_intervals(ivs)
        self.assertEqual(
            [(s.isoformat(), e.isoformat()) for s, e in merged],
            [
                ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:12+00:00"),
                ("2026-01-01T00:00:20+00:00", "2026-01-01T00:00:30+00:00"),
            ],
        )
        self.assertEqual(union_duration_seconds(ivs), 22)
        secs = seconds_in_intervals(merged)
        self.assertEqual(len(secs), 24)
        self.assertEqual(secs[0].isoformat(), "2026-01-01T00:00:00+00:00")
        self.assertEqual(secs[-1].isoformat(), "2026-01-01T00:00:30+00:00")
        self.assertEqual(merge_intervals([]), [])
        self.assertEqual(union_duration_seconds([]), 0)
        self.assertEqual(seconds_in_intervals([]), [])

    def test_expand_window_with_minutes(self):
        start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        end = start + timedelta(minutes=10)
        minute_ts = [start + timedelta(minutes=i) for i in range(10)]
        distances = np.array([[10, 10, 10, 4, 3, 4, 4.9, 10, 10, 10]], dtype=float)
        win_s = start + timedelta(minutes=4)
        win_e = start + timedelta(minutes=5)
        exp_s, exp_e = expand_window_with_minutes(
            win_s, win_e, 0, minute_ts, distances, 5.0, start, end,
        )
        self.assertEqual(exp_s.isoformat(), "2026-01-01T00:03:00+00:00")
        self.assertEqual(exp_e.isoformat(), "2026-01-01T00:07:00+00:00")

    def test_minute_grid_matches_ceil_duration_over_60(self):
        start = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
        end = datetime(2026, 9, 19, 0, 3, tzinfo=UTC)
        grid = minute_grid(start, end)
        self.assertEqual([iso_utc(ts) for ts in grid], [
            "2026-09-19T00:00:00Z",
            "2026-09-19T00:01:00Z",
            "2026-09-19T00:02:00Z",
        ])

    def test_parse_request_window_errors(self):
        with self.assertRaises(WindowError) as ctx:
            parse_request_window("nope", "2026-09-19T00:03:00Z")
        self.assertEqual(
            str(ctx.exception),
            "Invalid datetime format: Invalid isoformat string: 'nope'",
        )
        with self.assertRaises(WindowError) as ctx:
            parse_request_window("2026-09-19T00:00:00", "2026-09-19T00:03:00Z")
        self.assertEqual(str(ctx.exception), "start_time and end_time must include timezone information")
        with self.assertRaises(WindowError) as ctx:
            parse_request_window("2026-09-19T00:03:00Z", "2026-09-19T00:00:00Z")
        self.assertEqual(str(ctx.exception), "end_time must be after start_time")
        with self.assertRaises(WindowError) as ctx:
            parse_request_window("2026-09-19T00:00:00Z", "2026-09-20T00:00:01Z")
        self.assertEqual(str(ctx.exception), "Requested window exceeds maximum of 24 hours")


class SatcatGoldens(unittest.TestCase):
    def test_association_and_classify(self):
        self.assertTrue(is_iss_associated_record({"ORBIT_TYPE": "DOC", "ORBIT_CENTER": "EARTH"}))
        self.assertTrue(is_iss_associated_record({"ORBIT_TYPE": "DOCKED"}))
        self.assertTrue(is_iss_associated_record({"ORBIT_TYPE": "D"}))
        self.assertTrue(is_iss_associated_record({"ORBIT_TYPE": "UNDOCKING"}))
        self.assertTrue(is_iss_associated_record({"Orbit Center": "ISS (ZARYA)"}))
        self.assertTrue(is_iss_associated_record({"OPS_STATUS": "DOCKED"}))
        self.assertTrue(is_iss_associated_record({"DATA_STATUS_CODE": "DOCK"}))
        self.assertTrue(is_iss_associated_record({"ORBIT_CENTER": "25544"}))
        self.assertTrue(is_iss_associated_record({"ORBIT_CENTER": "100057"}))
        self.assertTrue(is_iss_associated_record({"ORBIT_CENTER": "ISS_ZARYA"}))
        self.assertFalse(is_iss_associated_record({"ORBIT_TYPE": "LEO", "ORBIT_CENTER": "EARTH"}))
        self.assertEqual(classify_norad("1", None, True), "UNKNOWN")
        self.assertEqual(classify_norad("100057", None, False), "ISS_ASSOCIATED")
        self.assertEqual(classify_norad("1", {"ORBIT_TYPE": "LEO"}, False), "EXTERNAL_CONJUNCTION")
        self.assertEqual(classify_norad("1", {"ORBIT_TYPE": "DOC"}, True), "ISS_ASSOCIATED")
        self.assertEqual(satcat_field({"orbit_type": "x"}, "ORBIT_TYPE"), "x")
        self.assertEqual(satcat_field({"ORBIT_TYPE": "  "}, "ORBIT_TYPE"), "")


class PayloadGoldens(unittest.TestCase):
    def test_vec3_rounding(self):
        self.assertEqual(vec3([1.23456, -2.0, 3]), {"x": 1.235, "y": -2.0, "z": 3.0})

    def test_data_quality_status(self):
        self.assertEqual(data_quality_status([], set(), []), "NO_CANDIDATES")
        self.assertEqual(data_quality_status(["1"], {"1"}, []), "PARTIAL")
        self.assertEqual(data_quality_status(["1"], set(), ["1"]), "PARTIAL")
        self.assertEqual(data_quality_status(["1"], set(), []), "COMPLETE")


class SocratesParserTests(unittest.TestCase):
    def test_parse_paired_rows(self):
        html = """
        <html><body>
        <table>
          <tr><td>hdr</td><td>NORAD</td><td>Name</td><td>x</td><td>TCA</td><td>Min</td><td>Speed</td></tr>
          <tr><td>1</td><td>25544</td><td>ISS</td><td>x</td><td>2026-09-19 00:00:00.000</td><td>0.125</td><td>0.000</td></tr>
          <tr><td>2</td><td>100057</td><td>SOYUZ-MS 29 [+]</td><td>x</td><td>1.662E-02</td><td>0.030</td><td></td></tr>
        </table>
        </body></html>
        """
        events = parse_socrates_html(html)
        self.assertEqual(events, [{
            "tca_utc": "2026-09-19 00:00:00.000",
            "min_range_km": "0.125",
            "rel_speed_km_s": "0.000",
            "iss_norad": "25544",
            "other_norad": "100057",
            "other_name": "SOYUZ-MS 29 [+]",
            "max_probability": "1.662E-02",
            "dilution_km": "0.030",
        }])

    def test_missing_table_returns_empty(self):
        self.assertEqual(parse_socrates_html("<html><body>nope</body></html>"), [])


class ValidationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_routes(self):
        paths = sorted(r.path for r in app.routes)
        self.assertIn("/health", paths)
        self.assertIn("/api/v1/orbits/positions", paths)
        self.assertIn("/api/v1/conjunctions/distances", paths)

    def test_validation_payloads(self):
        cases = [
            (
                "/api/v1/orbits/positions",
                {"start_time": "nope", "end_time": "2026-09-19T00:03:00Z"},
                "Invalid datetime format: Invalid isoformat string: 'nope'",
            ),
            (
                "/api/v1/conjunctions/distances",
                {"start_time": "2026-09-19T00:00:00", "end_time": "2026-09-19T00:03:00Z"},
                "start_time and end_time must include timezone information",
            ),
            (
                "/api/v1/orbits/positions",
                {"start_time": "2026-09-19T00:03:00Z", "end_time": "2026-09-19T00:00:00Z"},
                "end_time must be after start_time",
            ),
            (
                "/api/v1/conjunctions/distances",
                {"start_time": "2026-09-19T00:00:00Z", "end_time": "2026-09-20T00:00:01Z"},
                "Requested window exceeds maximum of 24 hours",
            ),
        ]
        for path, body, message in cases:
            r = self.client.post(path, json=body)
            self.assertEqual(r.status_code, 422, path)
            self.assertEqual(
                r.json(),
                {"error": {"code": "VALIDATION_ERROR", "message": message, "details": None}},
            )

        r = self.client.post("/api/v1/conjunctions/distances", json={
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:03:00Z",
            "critical_distance_km": 0,
        })
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["message"], "critical_distance_km must be > 0")

        r = self.client.post("/api/v1/orbits/positions", json={})
        self.assertEqual(r.status_code, 422)
        self.assertIn("detail", r.json())

    def test_orjson_optional_flag_unchanged_in_this_env(self):
        self.assertFalse(HAS_ORJSON)


def _ctx():
    epoch_iss = datetime(2026, 9, 18, 18, 0, tzinfo=UTC)
    epoch_deb = datetime(2026, 9, 18, 19, 0, tzinfo=UTC)
    retrieved = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    return {
        "events": [{"other_norad": "99999", "tca_utc": "2026-09-19 00:01:00.000"}],
        "candidate_ids": ["99999"],
        "omm_entries": {
            "25544": {"satrec": object(), "name": "ISS (ZARYA)", "epoch": epoch_iss},
            "99999": {"satrec": object(), "name": "DEB-A", "epoch": epoch_deb},
        },
        "failed_omm": [],
        "warnings": [],
        "socrates_retrieved_at": retrieved,
        "classifications": {"99999": "EXTERNAL_CONJUNCTION"},
        "associated_ids": [],
    }


def _propagate_factory(iss_pos, iss_vel, deb_pos, deb_vel):
    def fake_propagate(sat_map, jd_arr, fr_arr):
        n = len(jd_arr)
        zeros = np.zeros(n)
        out = {}
        if "25544" in sat_map:
            ip = np.resize(iss_pos, (n, 3)).copy()
            iv = np.resize(iss_vel, (n, 3)).copy()
            if n > iss_pos.shape[0]:
                ip[iss_pos.shape[0]:] = iss_pos[-1]
                iv[iss_vel.shape[0]:] = iss_vel[-1]
            else:
                ip = iss_pos[:n]
                iv = iss_vel[:n]
            out["25544"] = (zeros, ip, iv)
        if "99999" in sat_map:
            dp = np.resize(deb_pos, (n, 3)).copy()
            dv = np.resize(deb_vel, (n, 3)).copy()
            if n > deb_pos.shape[0]:
                dp[deb_pos.shape[0]:] = deb_pos[-1]
                dv[deb_vel.shape[0]:] = deb_vel[-1]
            else:
                dp = deb_pos[:n]
                dv = deb_vel[:n]
            out["99999"] = (zeros, dp, dv)
        return out
    return fake_propagate


class MockedEndpointGoldens(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.iss_pos = np.array([[1.0, 2.0, 3.0], [1.1, 2.1, 3.1], [1.2, 2.2, 3.2]])
        cls.iss_vel = np.array([[0.1, 0.2, 0.3], [0.1, 0.2, 0.3], [0.1, 0.2, 0.3]])
        cls.deb_pos = np.array([[4.0, 5.0, 6.0], [2.0, 3.0, 4.0], [10.0, 10.0, 10.0]])
        cls.deb_vel = np.array([[0.4, 0.5, 0.6], [0.4, 0.5, 0.6], [0.4, 0.5, 0.6]])

    def test_positions_sample_shape_and_values(self):
        fake = _propagate_factory(self.iss_pos, self.iss_vel, self.deb_pos, self.deb_vel)
        with patch("positions.load_orbit_context", new=AsyncMock(return_value=_ctx())), \
             patch("positions.propagate_all", side_effect=fake):
            r = self.client.post("/api/v1/orbits/positions", json={
                "start_time": "2026-09-19T00:00:00Z",
                "end_time": "2026-09-19T00:03:00Z",
            })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(list(body.keys()), [
            "request", "coordinate_frame", "position_unit", "velocity_unit",
            "source", "samples", "data_quality",
        ])
        self.assertEqual(body["request"], {
            "start_time": "2026-09-19T00:00:00Z",
            "end_time": "2026-09-19T00:03:00Z",
            "step_seconds": 60,
        })
        self.assertEqual(body["coordinate_frame"], "TEME")
        self.assertEqual(body["source"]["retrieved_at"], "2026-09-19T12:00:00Z")
        self.assertEqual(len(body["samples"]), 3)
        first = body["samples"][0]
        self.assertEqual(list(first.keys()), ["timestamp", "iss", "screened_objects"])
        self.assertEqual(first["iss"]["norad_id"], 25544)
        self.assertEqual(first["iss"]["position_km"], {"x": 1.0, "y": 2.0, "z": 3.0})
        self.assertEqual(first["screened_objects"][0]["norad_id"], 99999)
        self.assertIsNone(first["screened_objects"][0]["object_type"])
        self.assertEqual(body["data_quality"]["status"], "COMPLETE")
        self.assertEqual(body["data_quality"]["elements_epoch"], "2026-09-18T18:00:00Z")
        self.assertEqual(body["data_quality"]["warnings"], [])

    def test_source_unavailable(self):
        with patch("positions.load_orbit_context", new=AsyncMock(side_effect=RuntimeError("SOCRATES unavailable: boom"))):
            r = self.client.post("/api/v1/orbits/positions", json={
                "start_time": "2026-09-19T00:00:00Z",
                "end_time": "2026-09-19T00:03:00Z",
            })
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {
            "error": {
                "code": "SOURCE_UNAVAILABLE",
                "message": "SOCRATES unavailable: boom",
                "details": None,
            }
        })

    def test_missing_iss_omm(self):
        ctx = _ctx()
        del ctx["omm_entries"]["25544"]
        with patch("positions.load_orbit_context", new=AsyncMock(return_value=ctx)):
            r = self.client.post("/api/v1/orbits/positions", json={
                "start_time": "2026-09-19T00:00:00Z",
                "end_time": "2026-09-19T00:03:00Z",
            })
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["error"]["code"], "ORBITAL_ELEMENTS_ERROR")

    def test_distances_minute_samples_and_summary_keys(self):
        fake = _propagate_factory(self.iss_pos, self.iss_vel, self.deb_pos, self.deb_vel)
        with patch("distances.load_orbit_context", new=AsyncMock(return_value=_ctx())), \
             patch("distances.propagate_all", side_effect=fake):
            r = self.client.post("/api/v1/conjunctions/distances", json={
                "start_time": "2026-09-19T00:00:00Z",
                "end_time": "2026-09-19T00:03:00Z",
                "critical_distance_km": 10.0,
            })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(list(body.keys()), [
            "request", "distance_unit", "speed_unit", "samples",
            "summary", "critical_intervals", "data_quality",
        ])
        self.assertEqual(body["request"]["critical_distance_km"], 10.0)
        self.assertEqual(len(body["samples"]), 3)
        self.assertEqual(list(body["samples"][0].keys()), [
            "timestamp", "nearest_object", "distance_km",
            "relative_speed_km_s", "is_critical",
        ])
        # minute 0: |[4,5,6]-[1,2,3]| = sqrt(27) = 5.196 rounded
        self.assertEqual(body["samples"][0]["distance_km"], 5.196)
        self.assertTrue(body["samples"][0]["is_critical"])
        self.assertEqual(body["samples"][0]["nearest_object"]["norad_id"], 99999)
        self.assertIn("Порог превышает область исходного скрининга SOCRATES", body["data_quality"]["warnings"][0])
        self.assertEqual(list(body["summary"].keys()), [
            "minimum_distance_km", "tca", "nearest_object", "critical_duration_seconds",
        ])
        self.assertIsInstance(body["critical_intervals"], list)

    def test_distances_without_threshold_leaves_is_critical_null(self):
        fake = _propagate_factory(self.iss_pos, self.iss_vel, self.deb_pos, self.deb_vel)
        with patch("distances.load_orbit_context", new=AsyncMock(return_value=_ctx())), \
             patch("distances.propagate_all", side_effect=fake):
            r = self.client.post("/api/v1/conjunctions/distances", json={
                "start_time": "2026-09-19T00:00:00Z",
                "end_time": "2026-09-19T00:03:00Z",
            })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["request"]["critical_distance_km"])
        self.assertIsNone(body["samples"][0]["is_critical"])
        self.assertEqual(body["critical_intervals"], [])


class CriticalIntervalGoldens(unittest.TestCase):
    def test_interval_close_and_open_end(self):
        ts = [datetime(2026, 1, 1, 0, 0, i, tzinfo=UTC) for i in range(5)]
        dist = np.array([[10.0, 1.0, 0.5, 2.0, 10.0]])
        speed = np.array([[1.0, 2.0, 3.0, 2.5, 1.0]])
        omm = {"1": {"name": "DEB"}}
        intervals, raw = build_critical_intervals(["1"], omm, ts, dist, speed, 3.0)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0]["start_time"], "2026-01-01T00:00:01Z")
        self.assertEqual(intervals[0]["end_time"], "2026-01-01T00:00:04Z")
        self.assertEqual(intervals[0]["tca"], "2026-01-01T00:00:02Z")
        self.assertEqual(intervals[0]["minimum_distance_km"], 0.5)
        self.assertEqual(intervals[0]["maximum_relative_speed_km_s"], 3.0)
        self.assertEqual(intervals[0]["object"]["object_type"], None)
        self.assertEqual(raw[0][0], ts[1])
        self.assertEqual(raw[0][1], ts[4])


if __name__ == "__main__":
    unittest.main()
