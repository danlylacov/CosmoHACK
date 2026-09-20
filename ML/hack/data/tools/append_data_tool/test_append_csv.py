import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from append_csv import merge_csv


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, fields, rows):
        path = self.root / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(fields)
            writer.writerows(rows)
        return path

    def read(self, path):
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            return reader.fieldnames, list(reader)

    def test_overlap_replaces_entire_row_and_repeated_merge_is_identical(self):
        fields = ["time_utc", "wind", "wind_count"]
        old = self.write("old.csv", fields, [
            ["2026-09-19T12:00:00Z", "400.123456789", "6"],
            ["2026-09-19T12:30:00Z", "500", "2"]])
        new = self.write("new.csv", fields, [
            ["2026-09-19T13:00:00Z", "600", "2"],
            ["2026-09-19T12:30:00Z", "550", "4"]])
        self.assertEqual(merge_csv(old, new, old), 3)
        _, rows = self.read(old)
        self.assertEqual([row["wind"] for row in rows], ["400.123456789", "550", "600"])
        self.assertEqual([row["wind_count"] for row in rows], ["6", "4", "2"])
        result = old.read_bytes()
        merge_csv(old, new, old)
        self.assertEqual(old.read_bytes(), result)

    def test_timezone_equivalence_unsorted_input_and_duplicates(self):
        old = self.write("old.csv", ["time_utc", "x"], [
            ["2026-09-19T15:00:00+03:00", "1"], ["2026-09-19T11:00:00Z", "2"]])
        new = self.write("new.csv", ["x", "time_utc"], [
            ["3", "2026-09-19T12:00:00Z"], ["4", "2026-09-19T12:00:00"]])
        output = self.root / "nested" / "merged.csv"
        self.assertEqual(merge_csv(old, new, output), 2)
        _, rows = self.read(output)
        self.assertEqual(rows, [{"time_utc": "2026-09-19T11:00:00Z", "x": "2"},
                                {"time_utc": "2026-09-19T12:00:00Z", "x": "4"}])
        self.assertEqual(len(self.read(old)[1]), 2)

    def test_schema_union_and_explicit_blanks(self):
        old = self.write("old.csv", ["time_utc", "x", "y"], [
            ["2026-09-19T11:30:00Z", "1", "2"], ["2026-09-19T12:00:00Z", "3", "4"]])
        new = self.write("new.csv", ["time_utc", "z", "x"], [["2026-09-19T12:00:00Z", "0", ""]])
        merge_csv(old, new, old)
        fields, rows = self.read(old)
        self.assertEqual(fields, ["time_utc", "x", "y", "z"])
        self.assertEqual(rows[0], {"time_utc": "2026-09-19T11:30:00Z", "x": "1", "y": "2", "z": ""})
        self.assertEqual(rows[1], {"time_utc": "2026-09-19T12:00:00Z", "x": "", "y": "", "z": "0"})

    def test_empty_tables_and_custom_timestamp_column_with_multiline_text(self):
        fields = ["issued_at_utc", "discussion"]
        old = self.write("old.csv", fields, [])
        new = self.write("new.csv", fields, [])
        self.assertEqual(merge_csv(old, new, old, "issued_at_utc"), 0)
        text = 'Прогноз, "обновление"\nВторая строка'
        self.write("new.csv", fields, [["2026-09-19T12:17:00Z", text]])
        merge_csv(old, new, old, "issued_at_utc")
        self.assertEqual(self.read(old)[1], [{"issued_at_utc": "2026-09-19T12:17:00Z", "discussion": text}])

    def test_invalid_input_leaves_database_untouched(self):
        old = self.write("old.csv", ["time_utc", "x"], [["2026-09-19T12:00:00Z", "1"]])
        original = old.read_bytes()
        new = self.root / "new.csv"
        for content in ("", "wrong,x\na,1\n", "time_utc,x,x\na,1,2\n",
                        "time_utc,x\nbad-time,1\n", "time_utc,x\n2026-09-19T12:00:00Z\n",
                        "time_utc,x\n2026-09-19T12:00:00Z,1,2\n", 'time_utc,x\n"unfinished'):
            with self.subTest(content=content):
                new.write_text(content, encoding="utf-8")
                with self.assertRaises((ValueError, csv.Error)):
                    merge_csv(old, new, old)
                self.assertEqual(old.read_bytes(), original)

    def test_failed_replace_keeps_database_and_removes_temporary_file(self):
        old = self.write("old.csv", ["time_utc", "x"], [["2026-09-19T12:00:00Z", "1"]])
        new = self.write("new.csv", ["time_utc", "x"], [["2026-09-19T12:00:00Z", "2"]])
        original = old.read_bytes()
        with patch("append_csv.os.replace", side_effect=OSError("write failure")):
            with self.assertRaises(OSError):
                merge_csv(old, new, old)
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_cli_updates_first_file_by_default(self):
        old = self.write("old.csv", ["time_utc", "x"], [["2026-09-19T12:00:00Z", "1"]])
        new = self.write("new.csv", ["time_utc", "x"], [["2026-09-19T12:30:00Z", "2"]])
        command = [sys.executable, str(Path(__file__).with_name("append_csv.py")), str(old), str(new)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.read(old)[1]), 2)
        self.assertIn("2 строк", result.stdout)


if __name__ == "__main__":
    unittest.main()
