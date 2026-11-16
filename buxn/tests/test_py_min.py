import importlib.util
import io
from types import SimpleNamespace
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class TestPythonStackOpcodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        impl_dir = Path(__file__).resolve().parents[2] / "impls" / "py-min"
        spec = importlib.util.spec_from_file_location("py_min_devices", impl_dir / "devices.py")
        devices = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(devices)
        spec = importlib.util.spec_from_file_location("py_min_vm", impl_dir / "vm.py")
        cls.vm = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"devices": devices}):
            spec.loader.exec_module(cls.vm)

    def test_stack_opcodes_in_all_modes(self):
        # Numeric opcodes keep this independent of the assembler opcode table.
        cases = [
            (0x03, [0x11, 0x22], [0x22]),          # NIP
            (0x04, [0x11, 0x22], [0x22, 0x11]),    # SWP
            (0x05, [0x11, 0x22, 0x33], [0x22, 0x33, 0x11]),  # ROT
            (0x06, [0x11], [0x11, 0x11]),          # DUP
            (0x07, [0x11, 0x22], [0x11, 0x22, 0x11]),  # OVR
        ]
        for opcode, operands, result in cases:
            for short in (False, True):
                for ret in (False, True):
                    for keep in (False, True):
                        instr = opcode | (0x20 if short else 0) | (0x40 if ret else 0) | (0x80 if keep else 0)
                        with self.subTest(instr=hex(instr)):
                            vm = self.vm.Uxn()
                            src, dst = (vm.rst, vm.wst) if ret else (vm.wst, vm.rst)
                            values = [v * 0x101 if short else v for v in operands]
                            expected = [v * 0x101 if short else v for v in result]
                            for value in [0x99] + values:
                                src.push(value, short)
                            vm._exec(instr, 0x100)
                            expected = [0x99] + (values if keep else []) + expected
                            actual = [src.pop(short) for _ in expected][::-1]
                            self.assertEqual(actual, expected)
                            self.assertEqual(src.ptr, 0)
                            self.assertEqual(dst.ptr, 0)

    def test_branch_benchmark_verifies_past_byte_boundary_and_final_pair(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location("py_min_asm", root / "impls/py-min/asm.py")
        asm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(asm)
        assembler = asm.Assembler()
        assembler.assemble((root / "benchmarks/cpu/branch.tal").read_text())
        # Exercise the full verifier without repeating the expensive sort.
        for corrupt in (False, True):
            with self.subTest(corrupt=corrupt):
                vm = self.vm.Uxn()
                vm.load_rom(assembler.rom_bytes())
                offset = assembler.labels["verify_loop"] - 0x103
                vm.ram[0x100:0x103] = bytes([0x40]) + offset.to_bytes(2, "big")
                vm.ram[0x2000:0x2800] = bytes(i // 8 for i in range(2048))
                if corrupt:
                    vm.ram[0x27ff] = 0
                output = io.BytesIO()
                with patch("sys.stdout", SimpleNamespace(buffer=output)):
                    vm.run()
                self.assertEqual(output.getvalue(), b"FAIL" if corrupt else b"OK")
                self.assertEqual(vm.devices.exit_code, 1 if corrupt else 0)
                self.assertEqual(vm.wst.ptr, 0)
