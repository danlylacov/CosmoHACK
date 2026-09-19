import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import update_dataset as tool


class UpdateDataset(unittest.TestCase):
    def batch(self, path, value='1'):
        path.mkdir(parents=True, exist_ok=True)
        (path/'results.csv').write_text('time_utc,x\n2024-05-10T00:00:00Z,' + value + '\n')
        (path/'results.json').write_text(json.dumps({
            'period': {'start_utc': '2024-05-10T00:00:00Z',
                       'available_until_exclusive_utc': '2024-05-10T00:30:00Z'},
            'series': [{'metric': 'x', 'samples': [
                {'time_utc': '2024-05-10T00:00:00Z', 'value': int(value)}]}],
        }))

    def initial(self, root, value='1'):
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            ready = Path(temporary)
            self.batch(ready, value)
            tool.publish(root, ready)
        return self.snapshot(root)

    def snapshot(self, root):
        self.assertEqual(sorted(p.name for p in root.iterdir()), ['results.csv', 'results.json'])
        for path in root.iterdir():
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())
        return tuple((root/name).read_bytes() for name in ('results.csv', 'results.json'))

    def test_repeated_publish_leaves_only_two_regular_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.initial(root)
            new = self.initial(root, '2')
            self.assertNotEqual(old, new)
            self.assertEqual(tool.current_files(root), (root/'results.csv', root/'results.json'))
            self.assertEqual(json.loads(new[1])['series'][0]['samples'][0]['value'], 2)

    def test_failed_or_strict_partial_download_keeps_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.initial(root)
            with patch.object(tool.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2)):
                self.assertEqual(tool.main(['--date', '2024-05-10', '--out', str(root), '--strict']), 2)
            self.assertEqual(self.snapshot(root), old)

    def test_second_writer_rejected_without_lock_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            descriptor = os.open(root, os.O_RDONLY)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaisesRegex(ValueError, 'уже обновляется'):
                    tool.main(['--date', '2024-05-10', '--out', str(root)])
                self.assertEqual(list(root.iterdir()), [])
            finally:
                os.close(descriptor)
            # A completed/failed invocation must release the lock.
            with tool.dataset_lock(root):
                pass

    def test_empty_batch_rejected_but_measured_zero_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'fresh.csv'
            path.write_text('time_utc,x,x_count\n2024-05-10T00:00:00Z,,0\n')
            with self.assertRaisesRegex(ValueError, 'не содержит числовых'):
                tool.check_fresh(path)
            path.write_text('time_utc,x,x_count\n2024-05-10T00:00:00Z,0,1\n')
            tool.check_fresh(path)

    def test_failed_json_merge_never_publishes_prepared_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self.initial(root)
            actual_run = subprocess.run

            def runner(command):
                if str(command[0]).endswith('download_and_convert/run.sh'):
                    fresh = Path(command[-1])
                    self.batch(fresh, '8')
                    (fresh/'results.json').write_text('{broken')
                    return subprocess.CompletedProcess(command, 0)
                return actual_run(command, stdout=subprocess.DEVNULL)

            with patch.object(tool.subprocess, 'run', side_effect=runner):
                with self.assertRaises(ValueError):
                    tool.main(['--date', '2024-05-10', '--out', str(root)])
            self.assertEqual(self.snapshot(root), old)

    def test_failed_second_replace_rolls_back_both_files(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                old = self.initial(root) if existing else None
                with tempfile.TemporaryDirectory(dir=root) as temporary:
                    ready = Path(temporary)
                    self.batch(ready, '8')
                    actual_replace = os.replace

                    def fail_second(source, target):
                        if source == ready/'results.json':
                            raise OSError('Simulated publication failure')
                        return actual_replace(source, target)

                    with patch.object(tool.os, 'replace', side_effect=fail_second):
                        with self.assertRaisesRegex(OSError, 'publication failure'):
                            tool.publish(root, ready)
                if existing:
                    self.assertEqual(self.snapshot(root), old)
                else:
                    self.assertEqual(list(root.iterdir()), [])

    def test_missing_half_or_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'results.csv').write_text('time_utc\n')
            with self.assertRaisesRegex(ValueError, 'оба файла'):
                tool.current_files(root)
            (root/'results.json').symlink_to('missing.json')
            with self.assertRaisesRegex(ValueError, 'ссылками'):
                tool.current_files(root)

    def test_full_pipeline_leaves_only_two_files_after_repeated_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual_run = subprocess.run
            value = '1'

            def runner(command):
                if str(command[0]).endswith('download_and_convert/run.sh'):
                    self.batch(Path(command[-1]), value)
                    return subprocess.CompletedProcess(command, 0)
                return actual_run(command, stdout=subprocess.DEVNULL)

            with patch.object(tool.subprocess, 'run', side_effect=runner):
                self.assertEqual(tool.main(['--date', '2024-05-10', '--out', str(root)]), 0)
                first = self.snapshot(root)
                value = '2'
                self.assertEqual(tool.main(['--date', '2024-05-10', '--out', str(root)]), 0)
                second = self.snapshot(root)
            self.assertNotEqual(first, second)
            self.assertIn(b',2', second[0])
            values = [point['value'] for series in json.loads(second[1])['series']
                      for point in series['samples']]
            self.assertEqual(values, [2])


if __name__ == '__main__':
    unittest.main()
