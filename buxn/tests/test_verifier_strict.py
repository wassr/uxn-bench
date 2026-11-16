import tempfile
import unittest
from pathlib import Path

from buxn.discovery import TestNode
from buxn.runner import ExecutionResult
from buxn.verifier import Verifier


class TestStrictCoredumpVerification(unittest.TestCase):
    def verify_dump(self, spec, flags=0, payload=b"", version=2):
        header = bytearray(16)
        header[:4] = b"UXNC"
        header[4] = version
        header[5] = 1
        header[9:11] = (0x123).to_bytes(2, "big")
        header[12:14] = flags.to_bytes(2, "big")
        data = bytes(header) + payload
        if version == 1:
            data = b"UXNC" + bytes([1, 1, 0]) + bytes(512 + 65536)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "core.dump"
            path.write_bytes(data)
            test = TestNode("test", "", "Test", path, {"verify": {"type": "coredump", **spec}})
            return Verifier().verify(test, ExecutionResult(0, "", "", {"coredump": path}, 0))

    def test_missing_sections_do_not_become_zero_state(self):
        for spec in ({"wst": [0]}, {"rst": [0]}, {"ram": {"0": [0]}}, {"cycles": 0}, {"dev": {"0": [0]}}):
            with self.subTest(spec=spec):
                self.assertFalse(self.verify_dump(spec).passed)

    def test_present_zero_stack_and_header_fields(self):
        self.assertTrue(self.verify_dump({"wst": [0], "wst_ptr": 1, "pc": 0x123, "halt_reason": 0, "exit_code": 0}, 1, bytes(256)).passed)
        self.assertFalse(self.verify_dump({"wst_ptr": 1, "pc": 999}).passed)
        self.assertFalse(self.verify_dump({"wst_ptr": 1, "halt_reason": 1}).passed)

    def test_unknown_field_fails_even_with_valid_assertion(self):
        self.assertFalse(self.verify_dump({"wst_ptr": 1, "pc_typo": 0}).passed)

    def test_v1_does_not_invent_v2_fields(self):
        self.assertTrue(self.verify_dump({"wst": [0]}, version=1).passed)
        self.assertFalse(self.verify_dump({"wst_ptr": 1, "pc": 0x100}, version=1).passed)
