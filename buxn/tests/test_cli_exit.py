import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


class TestTestCommandExitStatus(unittest.TestCase):
    def run_cli(self, outcomes, options):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            tests_dir = workdir / "tests"
            tests_dir.mkdir()
            config = {
                "defaults": {"assembler": "fake"},
                "assemblers": {"fake": {
                    "cmd": shlex.join([sys.executable, "-c", "print('OK')"]),
                    "capabilities": ["assembler"],
                }},
            }
            (workdir / "buxn.json").write_text(json.dumps(config))
            for index, outcome in enumerate(outcomes):
                fixture = {
                    "run": {"input": "unused.tal"},
                    "capabilities": ["unavailable" if outcome == "skip" else "assembler"],
                    # Multiple failed assertions must still count as one failed test.
                    "verify": [
                        {"type": "stdout_match", "expected_stdout": "missing" if outcome == "fail" else "OK"},
                        {"type": "stdout_match", "expected_stdout": "also missing" if outcome == "fail" else "OK"},
                    ],
                }
                if outcome == "malformed":
                    (tests_dir / f"{index}.test.json").write_text("{broken")
                elif outcome == "invalid":
                    fixture["verify"] = []
                    (tests_dir / f"{index}.test.json").write_text(json.dumps(fixture))
                else:
                    (tests_dir / f"{index}.test.json").write_text(json.dumps(fixture))
            env = dict(os.environ)
            env["PYTHONPATH"] = str(root)
            return subprocess.run(
                [sys.executable, "-m", "buxn", *options], cwd=workdir,
                env=env, capture_output=True, text=True, timeout=15,
            )

    def test_failed_test_count_in_each_output_mode(self):
        modes = [[], ["test"], ["test", "--plain"], ["test", "--quiet"]]
        cases = [
            ([], 0),
            (["pass", "skip"], 0),
            (["skip", "skip"], 0),
            (["fail", "pass", "skip", "fail"], 2),
        ]
        for mode in modes:
            for outcomes, expected in cases:
                with self.subTest(mode=mode, outcomes=outcomes):
                    result = self.run_cli(outcomes, mode)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_fail_fast_counts_only_executed_failures(self):
        for mode in ([], ["--plain"], ["--quiet"]):
            with self.subTest(mode=mode):
                result = self.run_cli(["skip", "pass", "fail", "fail"], ["test", *mode, "--fail-fast"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_invalid_fixtures_exit_nonzero_without_traceback(self):
        for outcome in ("malformed", "invalid"):
            with self.subTest(outcome=outcome):
                result = self.run_cli([outcome], ["test", "--plain"])
                self.assertEqual(result.returncode, 1)
                self.assertIn("Invalid fixture", result.stdout)
                self.assertNotIn("Traceback", result.stderr)
