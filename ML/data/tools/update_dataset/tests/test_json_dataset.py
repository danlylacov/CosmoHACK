import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL))
from json_dataset import merge_json, iter_series

spec = importlib.util.spec_from_file_location("dataset_converter", TOOL.parent / "space_weather_model_data" / "series_to_csv.py")
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


def document(start, end, *series):
    return {"period": {"start_utc": start, "available_until_exclusive_utc": end}, "series": list(series)}


def series(metric, *samples):
    return {"metric": metric, "samples": list(samples)}


def sample(time, value, **quality):
    point = {"time_utc": time, "value": value}
    if quality:
        point["quality"] = quality
    return point


class MergeJsonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def put(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def rows(self, path):
        output = self.root / "converted.csv"
        converter.convert(json.loads(path.read_text()), output)
        with output.open() as stream:
            return list(csv.DictReader(stream))

    def test_initialize_normalizes_utc_and_preserves_separate_series(self):
        fresh = self.put("fresh.json", document("2024-05-10T03:00:00+03:00", "2024-05-10T03:30:00+03:00",
            series("a", sample("2024-05-10T03:01:00+03:00", 2)),
            series("a", sample("2024-05-10T00:02:00Z", 8))))
        target = self.root / "merged.json"
        stats = merge_json(None, fresh, target)
        self.assertEqual((stats["series"], stats["samples"]), (2, 2))
        data = json.loads(target.read_text())
        self.assertEqual(data["series"][0]["samples"][0]["time_utc"], "2024-05-10T00:01:00Z")
        self.assertEqual(float(self.rows(target)[0]["a"]), 5)

    def test_fresh_whole_row_wins_even_for_missing_metric(self):
        old = self.put("old.json", document("2024-05-10T00:00:00Z", "2024-05-10T01:00:00Z",
            series("a", sample("2024-05-10T00:01:00Z", 1), sample("2024-05-10T00:31:00Z", 2)),
            series("b", sample("2024-05-10T00:31:00Z", 9))))
        new = self.put("new.json", document("2024-05-10T00:35:00Z", "2024-05-10T00:40:00Z",
            series("a", sample("2024-05-10T00:36:00Z", None))))
        stats = merge_json(old, new, old)
        self.assertEqual(stats["samples"], 2)
        rows = self.rows(old)
        self.assertEqual(rows[0]["a"], "1.0")
        self.assertEqual((rows[1]["a"], rows[1]["b"]), ("", ""))

    def test_long_interval_split_retains_values_without_fast_statistics(self):
        old = self.put("old.json", document("2024-05-10T00:10:00Z", "2024-05-10T03:00:00Z",
            series("geomagnetic_kp", sample("2024-05-10T00:10:00Z", 3))))
        new = self.put("new.json", document("2024-05-10T00:30:00Z", "2024-05-10T01:00:00Z",
            series("geomagnetic_kp", sample("2024-05-10T00:30:00Z", 8, interval_seconds=1800))))
        target = self.root / "result.json"
        merge_json(old, new, target)
        rows = self.rows(target)
        self.assertEqual([float(row["geomagnetic_kp"]) for row in rows], [3, 8, 3, 3, 3, 3])
        self.assertNotIn("geomagnetic_kp_count", rows[0])
        points = json.loads(target.read_text())["series"][0]["samples"]
        self.assertEqual(points[0]["quality"]["original_interval_seconds"], 10800)

    def test_disjoint_dates_do_not_create_rows_in_gap_and_can_update_again(self):
        old = self.put("old.json", document("2024-05-10T00:00:00Z", "2024-05-10T00:30:00Z",
            series("a", sample("2024-05-10T00:01:00Z", 1))))
        new = self.put("new.json", document("2024-05-12T00:00:00Z", "2024-05-12T00:30:00Z",
            series("a", sample("2024-05-12T00:01:00Z", 2))))
        target = self.root / "result.json"
        merge_json(old, new, target)
        merge_json(target, new, target)
        self.assertEqual([r["a"] for r in self.rows(target)], ["1.0", "2.0"])
        self.assertEqual(sum(len(samples) for _, samples in [(m, list(s)) for m, s in iter_series(target)]), 2)

    def test_metadata_after_samples_and_invalid_input_preserves_output(self):
        fresh = self.put("fresh.json", document("2024-05-10T00:00:00Z", "2024-05-10T00:30:00Z",
            {"samples": [sample("2024-05-10T00:01:00Z", 7)], "metric": "a", "unit": "nT"}))
        target = self.root / "result.json"
        merge_json(None, fresh, target)
        self.assertEqual(json.loads(target.read_text())["series"][0]["unit"], "nT")
        previous = target.read_bytes()
        fresh.write_text(fresh.read_text()[:-3])
        with self.assertRaises(ValueError):
            merge_json(target, fresh, target)
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(list(self.root.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
