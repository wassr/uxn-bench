import hashlib
import shutil
import shlex
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Dict

from .logger import get_logger
from .discovery import TestNode

logger = get_logger(__name__)

BUXN_TMP_DIR = Path(".buxn/tmp")

@dataclass
class ExecutionResult:
    returncode: int
    stdout: str
    stderr: str
    output_files: Dict[str, Path]
    duration_ms: float

class Runner:
    def __init__(self, config: dict):
        self.config = config
        self.bin_dir = Path(".buxn/bin")
        BUXN_TMP_DIR.mkdir(parents=True, exist_ok=True)

    def _tool_spec(self, kind: str, name: str) -> dict:
        return self.config.get(kind, {}).get(name, {})

    def _build_argv(self, tool_name: str, cmd_template: str, tokens: dict) -> list:
        substituted = cmd_template
        for key, value in tokens.items():
            substituted = substituted.replace(f"{{{key}}}", str(value))

        argv = shlex.split(substituted)
        if argv and argv[0] == tool_name:
            vendored = self.bin_dir / tool_name
            if vendored.exists():
                argv[0] = str(vendored)
        return argv

    def tool_identity(self, tool_name: str, spec: dict) -> dict:
        """Record the command, executable/script hashes, and supplied build details."""
        argv = self._build_argv(tool_name, spec.get("cmd", tool_name), {})
        files = {}
        for index, arg in enumerate(argv):
            candidate = shutil.which(arg) if index == 0 else arg
            if candidate and Path(candidate).is_file():
                path = Path(candidate).resolve()
                files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"command": argv, "files": files, "build_metadata": spec.get("build_metadata", {})}

    def run_test(self, test: TestNode, timeout: float = 2.0) -> ExecutionResult:
        test_type = test.config.get("type", "assembler")

        asm_tool_name = self.config.get("defaults", {}).get("assembler", "uxnasm")
        asm_spec = self._tool_spec("assemblers", asm_tool_name)
        asm_cmd_template = asm_spec.get("cmd", f"{asm_tool_name} {{input}} {{output}}")

        test_tmp_dir = BUXN_TMP_DIR / test.id
        test_tmp_dir.mkdir(parents=True, exist_ok=True)

        run_spec = test.config.get("run", {})
        input_file = test.path.parent / run_spec.get("input", "")
        out_rom = test_tmp_dir / "out.rom"
        dump_file = test_tmp_dir / "core.dump"
        dump_file.unlink(missing_ok=True)  # avoid picking up a stale dump from a prior run

        # Test-specific timeout overrides command-line/global default
        test_timeout = run_spec.get("timeout", timeout)

        # Stage 1: Always assemble first
        cmd = self._build_argv(asm_tool_name, asm_cmd_template, {
            "input": input_file,
            "output": out_rom,
        })
        start_time = time.time()

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=test_timeout)
            returncode = res.returncode
            stdout = res.stdout
            stderr = res.stderr
        except subprocess.TimeoutExpired:
            return ExecutionResult(-1, "", f"Assembler TimeoutExpired after {test_timeout}s", {}, (time.time() - start_time) * 1000)
        except Exception as e:
            return ExecutionResult(-1, "", str(e), {}, (time.time() - start_time) * 1000)

        # If it's an interpreter test and assembly succeeded, run stage 2
        if test_type == "interpreter" and returncode == 0 and out_rom.exists():
            int_tool_name = self.config.get("defaults", {}).get("interpreter", "uxncli")
            int_spec = self._tool_spec("interpreters", int_tool_name)
            int_cmd_template = int_spec.get("cmd", f"{int_tool_name} {{rom}}")
            supports_coredump = "cpu/coredump" in int_spec.get("capabilities", [])

            cmd_int = self._build_argv(int_tool_name, int_cmd_template, {
                "rom": out_rom,
                "dump_file": dump_file if supports_coredump else "",
            })
            stdin_data = run_spec.get("stdin")

            try:
                res_int = subprocess.run(cmd_int, input=stdin_data, capture_output=True, text=True, timeout=test_timeout)
                returncode = res_int.returncode
                stdout = res_int.stdout
                stderr = res_int.stderr
            except subprocess.TimeoutExpired:
                return ExecutionResult(-1, stdout, f"Interpreter TimeoutExpired after {test_timeout}s", {"rom": out_rom}, (time.time() - start_time) * 1000)
            except Exception as e:
                return ExecutionResult(-1, stdout, f"Interpreter Error: {str(e)}", {"rom": out_rom}, (time.time() - start_time) * 1000)

        duration_ms = (time.time() - start_time) * 1000

        output_files = {}
        if out_rom.exists():
            output_files["rom"] = out_rom
        if dump_file.exists():
            output_files["coredump"] = dump_file

        return ExecutionResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            output_files=output_files,
            duration_ms=duration_ms
        )

    def run_benchmark(self, bench: TestNode, int_tool_name: str, runs: int = 5, timeout: float = 300.0) -> dict:
        import statistics
        import math

        if runs < 1:
            raise ValueError("Benchmark runs must be at least 1")

        # Stage 1: Write empty.tal & compile empty.rom to compute baseline process overhead
        empty_tal_path = BUXN_TMP_DIR / "empty.tal"
        empty_rom_path = BUXN_TMP_DIR / "empty.rom"
        with open(empty_tal_path, "w") as f:
            f.write("|0100 #80 #0f DEO\n")

        asm_tool_name = self.config.get("defaults", {}).get("assembler", "uxnasm")
        asm_spec = self._tool_spec("assemblers", asm_tool_name)
        asm_cmd_template = asm_spec.get("cmd", f"{asm_tool_name} {{input}} {{output}}")

        # Compile empty.rom
        cmd_asm_empty = self._build_argv(asm_tool_name, asm_cmd_template, {
            "input": empty_tal_path,
            "output": empty_rom_path,
        })
        subprocess.run(cmd_asm_empty, capture_output=True, check=True)

        # Assemble the benchmark ROM
        bench_tmp_dir = BUXN_TMP_DIR / "benchmarks" / bench.id
        bench_tmp_dir.mkdir(parents=True, exist_ok=True)
        bench_input = bench.path.parent / bench.config.get("run", {}).get("input", "")
        bench_rom = bench_tmp_dir / "out.rom"

        cmd_asm_bench = self._build_argv(asm_tool_name, asm_cmd_template, {
            "input": bench_input,
            "output": bench_rom,
        })
        subprocess.run(cmd_asm_bench, capture_output=True, check=True)

        # Get interpreter spec
        int_spec = self._tool_spec("interpreters", int_tool_name)
        int_cmd_template = int_spec.get("cmd", f"{int_tool_name} {{rom}}")
        supports_coredump = "cpu/coredump" in int_spec.get("capabilities", [])
        dump_file = bench_tmp_dir / "core.dump"
        dump_file.unlink(missing_ok=True)

        identity = self.tool_identity(int_tool_name, int_spec)
        assembler_identity = self.tool_identity(asm_tool_name, asm_spec)
        rom_hash = hashlib.sha256(bench_rom.read_bytes()).hexdigest()

        # Measure baseline process overhead by running empty.rom N=5 times
        cmd_empty = self._build_argv(int_tool_name, int_cmd_template, {
            "rom": empty_rom_path,
            "dump_file": dump_file if supports_coredump else "",
        })

        overhead_runs = []
        for _ in range(5):
            t_start = time.perf_counter()
            subprocess.run(cmd_empty, capture_output=True, check=True, timeout=timeout)
            t_end = time.perf_counter()
            overhead_runs.append((t_end - t_start) * 1000.0)  # ms

        overhead_median = statistics.median(overhead_runs)
        dump_file.unlink(missing_ok=True)  # baseline state must not verify the workload

        # Build runner command for the benchmark
        cmd_bench = self._build_argv(int_tool_name, int_cmd_template, {
            "rom": bench_rom,
            "dump_file": dump_file if supports_coredump else "",
        })
        stdin_data = bench.config.get("run", {}).get("stdin")

        from .verifier import Verifier
        verifier = Verifier()

        def execute_workload():
            dump_file.unlink(missing_ok=True)
            start = time.perf_counter()
            result = subprocess.run(cmd_bench, input=stdin_data, capture_output=True, text=True, timeout=timeout)
            duration = (time.perf_counter() - start) * 1000.0
            files = {"rom": bench_rom}
            if dump_file.exists():
                files["coredump"] = dump_file
            verification = verifier.verify(bench, ExecutionResult(
                result.returncode, result.stdout, result.stderr, files, duration))
            if not verification.passed:
                details = "; ".join(rule.details for rule in verification.rules if not rule.passed)
                raise ValueError(f"Benchmark verification failed: {details}")
            return result, duration

        # Each execution has independent output and verification, outside the timer.
        _, cold_start_raw = execute_workload()
        cold_start_adjusted = max(0.0, cold_start_raw - overhead_median)
        execute_workload()
        warm_runs_raw = []
        for _ in range(runs):
            last_res, duration = execute_workload()
            warm_runs_raw.append(duration)
        warm_runs_adjusted = [max(0.0, r - overhead_median) for r in warm_runs_raw]

        # Compute statistics
        def stats(runs_list):
            if not runs_list:
                return 0.0, 0.0, 0.0, 0.0, 0.0
            avg_val = sum(runs_list) / len(runs_list)
            median_val = statistics.median(runs_list)
            min_val = min(runs_list)
            max_val = max(runs_list)
            variance = sum((x - avg_val) ** 2 for x in runs_list) / len(runs_list)
            stddev = math.sqrt(variance)
            return avg_val, median_val, min_val, max_val, stddev

        avg_raw, median_raw, min_raw, max_raw, stddev_raw = stats(warm_runs_raw)
        avg_adj, median_adj, min_adj, max_adj, stddev_adj = stats(warm_runs_adjusted)

        # Clean up temp empty ROM files
        empty_tal_path.unlink(missing_ok=True)
        empty_rom_path.unlink(missing_ok=True)

        return {
            "benchmark": bench.id,
            "rom_sha256": rom_hash,
            "interpreter_identity": identity,
            "assembler_identity": assembler_identity,
            "benchmark_revision": bench.config.get("revision", 1),
            "interpreter": int_tool_name,
            "interpreter_version": int_spec.get("version", "unknown"),
            "verified": True,
            "overhead_ms": overhead_median,
            "overhead_runs": [round(x, 2) for x in overhead_runs],
            "cold_start_raw_ms": round(cold_start_raw, 2),
            "cold_start_adjusted_ms": round(cold_start_adjusted, 2),
            "warm_runs_raw_ms": [round(x, 2) for x in warm_runs_raw],
            "warm_runs_adjusted_ms": [round(x, 2) for x in warm_runs_adjusted],
            "avg_warm_raw_ms": round(avg_raw, 2),
            "avg_warm_adjusted_ms": round(avg_adj, 2),
            "median_warm_raw_ms": round(median_raw, 2),
            "median_warm_adjusted_ms": round(median_adj, 2),
            "min_warm_raw_ms": round(min_raw, 2),
            "min_warm_adjusted_ms": round(min_adj, 2),
            "max_warm_raw_ms": round(max_raw, 2),
            "max_warm_adjusted_ms": round(max_adj, 2),
            "stddev_ms": round(stddev_adj, 2),
            "stdout": last_res.stdout,
            "stderr": last_res.stderr,
            "returncode": last_res.returncode
        }
