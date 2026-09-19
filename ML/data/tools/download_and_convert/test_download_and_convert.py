import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('combined', Path(__file__).with_name('download_and_convert.py'))
combined = importlib.util.module_from_spec(spec)
spec.loader.exec_module(combined)


class CombinedTests(unittest.TestCase):
    def invoke(self, mode, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'results.json'
            target = root / 'results.csv'
            source.write_text('old json')
            target.write_text('old csv')
            calls = []
            def run(command):
                calls.append(command)
                if len(calls) == 1:
                    if mode not in ('failed', 'plan', 'help'):
                        new = root / 'new.json'
                        new.write_text(json.dumps({'period': {}, 'series': []}))
                        new.replace(source)
                    return subprocess.CompletedProcess(command, 2 if mode in ('failed', 'partial') else 0)
                Path(command[-1]).write_text('new csv')
                return subprocess.CompletedProcess(command, 1 if mode == 'conversion_error' else 0)
            args = ['--date', '2024-05-10', '--out', str(root), *extra]
            with patch.object(combined.subprocess, 'run', side_effect=run):
                code = combined.main(args)
            self.assertEqual(calls[0][1:], args)
            self.assertFalse(list(root.glob('.results-*.csv')))
            return code, target.read_text(), len(calls)

    def test_success_forwards_args_and_converts(self):
        self.assertEqual(self.invoke('success'), (0, 'new csv', 2))

    def test_strict_partial_converts_but_keeps_exit_two(self):
        self.assertEqual(self.invoke('partial', ['--strict']), (2, 'new csv', 2))

    def test_failed_download_does_not_convert_previous_json(self):
        self.assertEqual(self.invoke('failed'), (2, 'old csv', 1))

    def test_failed_conversion_preserves_previous_csv(self):
        self.assertEqual(self.invoke('conversion_error'), (1, 'old csv', 2))

    def test_plan_and_help_do_not_convert(self):
        for mode, flag in [('plan', '--plan-only'), ('help', '--help')]:
            self.assertEqual(self.invoke(mode, [flag]), (0, 'old csv', 1))


if __name__ == '__main__':
    unittest.main()
