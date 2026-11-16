import unittest
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from buxn.discovery import TestNode

from buxn.runner import Runner

class TestRunner(unittest.TestCase):
    def setUp(self):
        self.config = {
            "defaults": {
                "assembler": "uxnasm",
                "interpreter": "uxn2"
            },
            "assemblers": {
                "uxnasm": {
                    "cmd": "uxnasm {input} {output}"
                }
            },
            "interpreters": {
                "uxn2": {
                    "cmd": "uxn2 {rom}"
                },
                "py-min": {
                    "cmd": "python3 vm.py {rom} --dump-on-halt {dump_file}",
                    "capabilities": ["cpu/coredump"]
                }
            }
        }
        self.runner = Runner(self.config)
        self.runner.bin_dir = Path("/tmp/fake_bin") # Prevent it from picking up real vendor binaries
        
    def test_build_argv_simple(self):
        cmd = self.runner._build_argv("uxnasm", "uxnasm {input} {output}", {
            "input": "in.tal",
            "output": "out.rom"
        })
        self.assertEqual(cmd, ["uxnasm", "in.tal", "out.rom"])
        
    def test_build_argv_with_dump(self):
        cmd = self.runner._build_argv("py-min", "python3 vm.py {rom} --dump-on-halt {dump_file}", {
            "rom": "out.rom",
            "dump_file": "core.dump"
        })
        self.assertEqual(cmd, ["python3", "vm.py", "out.rom", "--dump-on-halt", "core.dump"])
        
    def test_build_argv_empty_token(self):
        cmd = self.runner._build_argv("py-min", "python3 vm.py {rom} {dump_file}", {
            "rom": "out.rom",
            "dump_file": ""
        })
        # shlex.split handles empty space
        self.assertEqual(cmd, ["python3", "vm.py", "out.rom"])

class TestBenchmarkRunner(unittest.TestCase):
    def test_coredump_baseline_and_text_stdin(self):
        config = {
            "defaults": {"assembler": "asm"},
            "assemblers": {"asm": {"cmd": "asm {input} {output}"}},
            "interpreters": {"vm": {
                "cmd": "vm {rom} --dump-on-halt {dump_file}",
                "capabilities": ["cpu/coredump"],
            }},
        }
        bench = TestNode("sample", "", "Sample", Path("sample.bench.json"), {
            "run": {"input": "sample.tal", "stdin": "hello"},
            "verify": {"type": "stdout_match", "expected_stdout": "OK"},
        })
        with tempfile.TemporaryDirectory() as tmp, patch("buxn.runner.BUXN_TMP_DIR", Path(tmp)):
            runner = Runner(config)
            def execute(argv, **kwargs):
                if argv[0] == "asm":
                    Path(argv[-1]).write_bytes(b"ROM")
                if argv[0] == "vm":
                    self.assertEqual(argv[-2], "--dump-on-halt")
                    self.assertTrue(argv[-1].endswith("core.dump"))
                    self.assertEqual(kwargs["timeout"], 123)
                    if "input" in kwargs:
                        if run.call_count == 8:
                            self.assertFalse(Path(argv[-1]).exists())
                        self.assertTrue(kwargs.get("text"))
                        self.assertEqual(kwargs["input"], "hello")
                    else:
                        self.assertTrue(kwargs.get("check"))
                        Path(argv[-1]).write_bytes(b"baseline dump")
                return subprocess.CompletedProcess(argv, 0, "OK", "")
            with patch("buxn.runner.subprocess.run", side_effect=execute) as run:
                result = runner.run_benchmark(bench, "vm", runs=1, timeout=123)
            self.assertTrue(result["verified"])
            self.assertEqual(run.call_count, 10)  # two assemblies, five baselines, three runs

    def test_every_execution_must_verify(self):
        config = {
            "defaults": {"assembler": "asm"},
            "assemblers": {"asm": {"cmd": "asm {input} {output}"}},
            "interpreters": {"vm": {"cmd": "vm {rom}"}},
        }
        bench = TestNode("sample", "", "Sample", Path("sample.bench.json"), {
            "run": {"input": "sample.tal"},
            "verify": {"type": "stdout_match", "expected_stdout": "OK"},
        })
        for fail_at in range(4):  # cold, warm-up, first and second measurements
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory() as tmp:
                with patch("buxn.runner.BUXN_TMP_DIR", Path(tmp)):
                    runner = Runner(config)
                    workload_count = 0
                    def execute(argv, **kwargs):
                        nonlocal workload_count
                        if argv[0] == "asm":
                            Path(argv[-1]).write_bytes(b"ROM")
                        output = "OK"
                        if argv[0] == "vm" and "input" in kwargs:
                            if workload_count == fail_at:
                                output = "WRONG"
                            workload_count += 1
                        return subprocess.CompletedProcess(argv, 0, output, "")
                    with patch("buxn.runner.subprocess.run", side_effect=execute):
                        with self.assertRaisesRegex(ValueError, "verification failed"):
                            runner.run_benchmark(bench, "vm", runs=2)
                    self.assertEqual(workload_count, fail_at + 1)

    def test_dump_cannot_survive_between_workload_executions(self):
        config = {
            "defaults": {"assembler": "asm"},
            "assemblers": {"asm": {"cmd": "asm {input} {output}"}},
            "interpreters": {"vm": {"cmd": "vm {rom} {dump_file}", "capabilities": ["cpu/coredump"]}},
        }
        bench = TestNode("sample", "", "Sample", Path("sample.bench.json"), {
            "run": {"input": "sample.tal"},
            "verify": {"type": "coredump", "wst_ptr": 0},
        })
        with tempfile.TemporaryDirectory() as tmp, patch("buxn.runner.BUXN_TMP_DIR", Path(tmp)):
            runner = Runner(config)
            count = 0
            def execute(argv, **kwargs):
                nonlocal count
                if argv[0] == "asm":
                    Path(argv[-1]).write_bytes(b"ROM")
                if argv[0] == "vm" and "input" in kwargs:
                    count += 1
                    if count == 1:
                        Path(argv[-1]).write_bytes(b"UXNC" + bytes([2]) + bytes(11))
                return subprocess.CompletedProcess(argv, 0, "", "")
            with patch("buxn.runner.subprocess.run", side_effect=execute):
                with self.assertRaisesRegex(ValueError, "No coredump"):
                    runner.run_benchmark(bench, "vm", runs=1)
            self.assertEqual(count, 2)

class TestToolIdentity(unittest.TestCase):
    def test_hashes_scripts_and_retains_build_details(self):
        import hashlib
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "interpreter.py"
            path.write_text("first build")
            with patch("buxn.runner.BUXN_TMP_DIR", Path(tmp)):
                runner = Runner({})
            spec = {"cmd": f"{sys.executable} {path} {{rom}}", "build_metadata": {"flags": "-O3", "revision": "abc"}}
            identity = runner.tool_identity("python", spec)
            self.assertEqual(identity["files"][str(path)], hashlib.sha256(b"first build").hexdigest())
            self.assertEqual(identity["build_metadata"], spec["build_metadata"])
            path.write_text("second build")
            self.assertNotEqual(identity["files"], runner.tool_identity("python", spec)["files"])

if __name__ == '__main__':
    unittest.main()
