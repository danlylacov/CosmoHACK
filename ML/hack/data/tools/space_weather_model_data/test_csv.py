import csv
import tempfile
import unittest
from pathlib import Path

import series_to_csv


def sample(time, value, quality=None):
    return {"time_utc": "2024-05-10T" + time + "Z", "value": value, "quality": quality}


def series(metric, *points):
    return {"metric": metric, "samples": list(points)}


class CsvTests(unittest.TestCase):
    def convert(self, converter, data):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "result.csv"
            converter(data, output)
            with output.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                return reader.fieldnames, list(reader)

    def measurements(self, *items, end="2024-05-11T00:00:00Z"):
        data = {"period": {"start_utc": "2024-05-10T00:00:00Z",
                           "available_until_exclusive_utc": end}, "series": list(items)}
        return self.convert(series_to_csv.convert, data)

    def test_fine_data_boundaries_missing_and_duplicates(self):
        fields, rows = self.measurements(series("wind", sample("00:00:00", 0),
            sample("00:05:00", -2), sample("00:05:00", -2), sample("00:15:00", None),
            sample("00:20:00", float("nan")), sample("00:25:00", float("inf")),
            sample("00:29:59", 8), sample("00:30:00", 12), sample("23:59:59", 3),
            sample("00:10:00", 7, "missing_or_invalid")))
        self.assertEqual(fields, ["time_utc", "wind", "wind_min", "wind_max", "wind_last", "wind_count"])
        self.assertEqual(len(rows), 48)
        self.assertEqual(float(rows[0]["wind"]), 3.25)
        self.assertEqual(float(rows[0]["wind_min"]), -2)
        self.assertEqual(float(rows[0]["wind_max"]), 8)
        self.assertEqual(float(rows[0]["wind_last"]), 8)
        self.assertEqual(rows[0]["wind_count"], "4")
        self.assertEqual(float(rows[1]["wind"]), 12)
        self.assertEqual(float(rows[1]["wind_min"]), 12)
        self.assertEqual(float(rows[1]["wind_max"]), 12)
        self.assertEqual(float(rows[1]["wind_last"]), 12)
        self.assertEqual(rows[1]["wind_count"], "1")
        self.assertEqual(rows[2]["wind"], "")
        self.assertEqual([rows[2]["wind_" + stat] for stat in ("min", "max", "last", "count")],
                         ["", "", "", "0"])
        self.assertEqual(rows[-1]["time_utc"], "2024-05-10T23:30:00Z")
        self.assertEqual(float(rows[-1]["wind"]), 3)

    def test_three_hour_indices_do_not_fill_gaps(self):
        fields, rows = self.measurements(series("geomagnetic_kp", sample("00:00:00", 7),
            sample("06:00:00", 5), sample("09:00:00", None), sample("21:00:00", 3)),
            series("geomagnetic_ap", sample("00:00:00", 100)))
        self.assertEqual(fields, ["time_utc", "geomagnetic_ap", "geomagnetic_kp"])
        self.assertTrue(all(float(row["geomagnetic_kp"]) == 7 for row in rows[:6]))
        self.assertTrue(all(row["geomagnetic_kp"] == "" for row in rows[6:12]))
        self.assertTrue(all(float(row["geomagnetic_kp"]) == 5 for row in rows[12:18]))
        self.assertTrue(all(row["geomagnetic_kp"] == "" for row in rows[18:42]))
        self.assertTrue(all(float(row["geomagnetic_kp"]) == 3 for row in rows[42:]))
        self.assertEqual(float(rows[5]["geomagnetic_ap"]), 100)
        self.assertEqual(rows[6]["geomagnetic_ap"], "")

    def test_explicit_hourly_daily_and_half_hour_intervals(self):
        fields, rows = self.measurements(
            series("daily", sample("00:00:00", 150, {"interval_seconds": 86400})),
            series("hourly", sample("00:00:00", 4,
                {"interval_end_utc": "2024-05-10T01:00:00Z"})),
            series("half_hour", sample("00:30:00", 8, {"interval_seconds": 1800})))
        self.assertEqual(fields, ["time_utc", "daily", "half_hour", "hourly"])
        self.assertTrue(all(float(row["daily"]) == 150 for row in rows))
        self.assertEqual([row["hourly"] for row in rows[:3]], ["4.0", "4.0", ""])
        self.assertEqual([row["half_hour"] for row in rows[:3]], ["", "8.0", ""])

    def test_sources_have_equal_weight_after_resampling(self):
        _, rows = self.measurements(series("wind", sample("00:00:00", 0), sample("00:05:00", 4)),
                                   series("wind", sample("00:00:00", 10)))
        self.assertEqual(float(rows[0]["wind"]), 6)
        self.assertEqual(float(rows[0]["wind_min"]), 0)
        self.assertEqual(float(rows[0]["wind_max"]), 10)
        self.assertEqual(float(rows[0]["wind_last"]), 4)
        self.assertEqual(rows[0]["wind_count"], "3")

    def test_period_cutoff_timezone_and_empty_inputs(self):
        item = series("wind", {"time_utc": "2024-05-10T03:30:00+03:00", "value": 5},
                      sample("00:35:00", 99), sample("00:40:00", 99))
        fields, rows = self.measurements(item, series("empty"), end="2024-05-10T00:35:00Z")
        self.assertEqual(fields, ["time_utc", "empty", "wind", "wind_min", "wind_max", "wind_last", "wind_count"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(float(rows[-1]["wind"]), 5)
        self.assertEqual(float(rows[-1]["wind_last"]), 5)
        self.assertEqual(rows[-1]["wind_count"], "1")
        fields, rows = self.measurements()
        self.assertEqual(fields, ["time_utc"])
        self.assertEqual(len(rows), 48)

    def test_last_uses_time_and_averages_simultaneous_sources(self):
        _, rows = self.measurements(
            series("wind", sample("00:25:00", 6, {"interval_seconds": 300}),
                sample("00:00:00", 100), sample("00:29:00", None)),
            series("wind", {"time_utc": "2024-05-10T03:25:00+03:00", "value": 10}))
        self.assertEqual(float(rows[0]["wind_last"]), 8)
        self.assertEqual(float(rows[0]["wind_min"]), 6)
        self.assertEqual(float(rows[0]["wind_max"]), 100)
        self.assertEqual(rows[0]["wind_count"], "3")

    def test_all_missing_fine_data_still_has_statistics(self):
        fields, rows = self.measurements(series("wind", sample("00:00:00", None),
            sample("00:05:00", float("nan")), sample("00:10:00", float("inf"))))
        self.assertIn("wind_count", fields)
        for row in rows:
            self.assertEqual([row[field] for field in fields[1:]], ["", "", "", "", "0"])

    def test_repeated_coarse_values_are_not_counted_as_measurements(self):
        _, rows = self.measurements(
            series("mixed", sample("00:00:00", 10, {"interval_seconds": 3600})),
            series("mixed", sample("00:05:00", 2), sample("00:10:00", 4)))
        self.assertEqual(float(rows[0]["mixed"]), 6.5)
        self.assertEqual([float(rows[0]["mixed_" + stat]) for stat in ("min", "max", "last", "count")],
                         [2, 4, 4, 2])
        self.assertEqual(float(rows[1]["mixed"]), 10)
        self.assertEqual([rows[1]["mixed_" + stat] for stat in ("min", "max", "last", "count")],
                         ["", "", "", "0"])

    def test_accumulated_dates_skip_unrequested_gaps(self):
        data = {
            "period": {
                "start_utc": "2024-05-10T00:00:00Z",
                "available_until_exclusive_utc": "2024-05-13T00:00:00Z",
                "included_ranges_utc": [
                    {"start_utc": "2024-05-12T00:00:00Z", "end_exclusive_utc": "2024-05-13T00:00:00Z"},
                    {"start_utc": "2024-05-10T00:00:00Z", "end_exclusive_utc": "2024-05-11T00:00:00Z"},
                ],
            },
            "series": [series("wind", sample("00:05:00", 2),
                {"time_utc": "2024-05-11T00:05:00Z", "value": 999},
                {"time_utc": "2024-05-12T00:05:00Z", "value": 4})],
        }
        fields, rows = self.convert(series_to_csv.convert, data)
        self.assertEqual(len(rows), 96)
        self.assertTrue(all(not row["time_utc"].startswith("2024-05-11") for row in rows))
        self.assertEqual(float(rows[0]["wind"]), 2)
        self.assertEqual(float(rows[48]["wind"]), 4)
        self.assertEqual(sum(int(row["wind_count"]) for row in rows), 2)
        data["period"]["included_ranges_utc"] = []
        _, rows = self.convert(series_to_csv.convert, data)
        self.assertEqual(rows, [])

    def test_split_coarse_interval_preserves_measurement_class(self):
        fields, rows = self.measurements(series("hourly", sample("00:10:00", 3,
            {"interval_seconds": 1200, "original_interval_seconds": 3600})))
        self.assertEqual(fields, ["time_utc", "hourly"])
        self.assertEqual(rows[0]["hourly"], "3.0")
        self.assertEqual(rows[1]["hourly"], "")

    def test_sparse_ranges_are_merged_and_must_align_with_windows(self):
        data = {"period": {"start_utc": "2024-05-10T00:00:00Z",
            "available_until_exclusive_utc": "2024-05-11T00:00:00Z",
            "included_ranges_utc": [
                {"start_utc": "2024-05-10T00:00:00Z", "end_exclusive_utc": "2024-05-10T01:00:00Z"},
                {"start_utc": "2024-05-10T00:30:00Z", "end_exclusive_utc": "2024-05-10T01:30:00Z"},
            ]}, "series": []}
        _, rows = self.convert(series_to_csv.convert, data)
        self.assertEqual(len(rows), 3)
        data["period"]["included_ranges_utc"][0]["start_utc"] = "2024-05-10T00:05:00Z"
        with self.assertRaises(ValueError):
            self.convert(series_to_csv.convert, data)


if __name__ == "__main__":
    unittest.main()
