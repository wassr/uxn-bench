import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from buxn import benchmark
from buxn.discovery import TestNode


class TestBenchmarkCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.baseline = Path(self.tmp.name) / "baseline.json"
        self.baseline.write_text(json.dumps({
            "timestamp": "now", "results": [{
                "benchmark": "cpu/example", "benchmark_revision": 1,
                "interpreter": "old", "interpreter_version": "1",
                "median_warm_adjusted_ms": 10,
            }],
        }))
        self.node = TestNode("cpu/example", "cpu", "Example", Path("example.bench.json"), {})
        self.args = SimpleNamespace(vs=str(self.baseline), interpreter=None, runs=1, timeout=1, include=None, exclude=None)
        self.config = {"interpreters": {"old": {}}}
        self.output = io.StringIO()
        self.enterContext(patch("sys.stdout", self.output))
        engine = self.enterContext(patch.object(benchmark, "DiscoveryEngine"))
        self.engine = engine.return_value
        self.engine.discover.return_value = [self.node]
        runner = self.enterContext(patch.object(benchmark, "Runner"))
        self.runner = runner.return_value
        self.enterContext(patch.object(benchmark, "get_latest_run_file", return_value=self.baseline))
        self.enterContext(patch.object(benchmark, "save_benchmark_run"))
        self.enterContext(patch.object(benchmark, "get_system_metadata", return_value={}))

    def test_compare_failures_never_announce_success(self):
        for result in ({"verified": False}, RuntimeError("crash")):
            with self.subTest(result=result):
                self.output.truncate(0)
                self.output.seek(0)
                self.runner.run_benchmark.side_effect = [result]
                self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 1)
                self.assertNotIn("stable or faster", self.output.getvalue())

    def test_empty_and_revision_mismatch_comparisons_fail(self):
        for nodes in ([], [self.node]):
            with self.subTest(nodes=nodes):
                self.node.config["revision"] = 2
                self.engine.discover.return_value = nodes
                self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 1)
        self.runner.run_benchmark.assert_not_called()

    def test_run_partial_failure_returns_nonzero(self):
        self.config["interpreters"]["bad"] = {}
        good = {
            "verified": True, "interpreter": "old", "interpreter_version": "1",
            "benchmark": "cpu/example", "overhead_ms": 1,
            "median_warm_raw_ms": 10, "median_warm_adjusted_ms": 9,
        }
        self.runner.run_benchmark.side_effect = [good, RuntimeError("crash")]
        self.assertEqual(benchmark.run_benchmarks(self.args, self.config), 1)
        self.assertIn("1 passed, 1 failed", self.output.getvalue())

    def test_successful_comparison_returns_zero(self):
        self.runner.run_benchmark.return_value = {"verified": True, "median_warm_adjusted_ms": 10}
        self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 0)
        self.assertIn("stable or faster", self.output.getvalue())

    def test_candidate_and_baseline_are_independent_and_filters_apply(self):
        self.config["interpreters"]["new"] = {}
        self.args.interpreter = "new"
        self.args.include = ["cpu/*"]
        self.args.exclude = ["cpu/slow"]
        self.runner.run_benchmark.return_value = {"verified": True, "median_warm_adjusted_ms": 9}
        self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 0)
        self.assertEqual(self.runner.run_benchmark.call_args.args[1], "new")
        self.engine.discover.assert_called_once_with(includes=["cpu/*"], excludes=["cpu/slow"], pattern="*.bench.json")

    def test_latest_is_scoped_to_selected_baseline(self):
        self.args.vs = "latest"
        self.args.interpreter = "old"
        self.args.baseline_interpreter = "reference"
        self.runner.run_benchmark.return_value = {"verified": True, "median_warm_adjusted_ms": 10}
        benchmark.compare_benchmarks(self.args, self.config)
        benchmark.get_latest_run_file.assert_called_once_with("reference")

    def test_explicit_baseline_does_not_require_history(self):
        benchmark.get_latest_run_file.return_value = None
        self.runner.run_benchmark.return_value = {"verified": True, "median_warm_adjusted_ms": 10}
        self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 0)
        benchmark.get_latest_run_file.assert_not_called()

    def test_different_roms_are_not_compared(self):
        data = json.loads(self.baseline.read_text())
        data["results"][0]["rom_sha256"] = "oldhash"
        self.baseline.write_text(json.dumps(data))
        self.runner.run_benchmark.return_value = {"verified": True, "rom_sha256": "newhash"}
        self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 1)
        self.assertIn("ROM hash mismatch", self.output.getvalue())
        self.assertNotIn("stable or faster", self.output.getvalue())

    def test_compare_checks_candidate_capabilities(self):
        self.node.config["capabilities"] = ["unsupported"]
        self.assertEqual(benchmark.compare_benchmarks(self.args, self.config), 1)
        self.runner.run_benchmark.assert_not_called()


class TestBenchmarkCLI(unittest.TestCase):
    def test_partial_run_and_failed_comparison_exit_nonzero(self):
        import os
        import shlex
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "benchmarks").mkdir()
            asm = shlex.join([sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'ROM')", "{output}"])
            config = {
                "defaults": {"assembler": "fake", "interpreter": "good"},
                "assemblers": {"fake": {"cmd": asm}},
                "interpreters": {
                    "good": {"cmd": shlex.join([sys.executable, "-c", "print('OK')"])},
                    "bad": {"cmd": shlex.join([sys.executable, "-c", "print('WRONG')"])},
                },
            }
            (workdir / "buxn.json").write_text(json.dumps(config))
            for name, expected in (("pass", "OK"), ("fail", "missing")):
                (workdir / "benchmarks" / f"{name}.bench.json").write_text(json.dumps({
                    "run": {"input": "unused.tal"},
                    "verify": {"type": "stdout_match", "expected_stdout": expected},
                }))
            env = {**os.environ, "PYTHONPATH": str(root)}
            def run(*options):
                return subprocess.run([sys.executable, "-m", "buxn", "bench", *options],
                                      cwd=workdir, env=env, text=True, capture_output=True, timeout=15)
            result = run("run", "--interpreter", "good", "--runs", "1")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("1 passed, 1 failed", result.stdout)
            result = run("compare", "--interpreter", "bad", "--baseline-interpreter", "good", "--include", "pass", "--runs", "1")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("0 compared, 1 failed", result.stdout)
            self.assertNotIn("stable or faster", result.stdout)
