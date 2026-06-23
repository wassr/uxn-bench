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
