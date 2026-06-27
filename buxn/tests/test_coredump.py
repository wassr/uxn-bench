import unittest
import tempfile
import os

from buxn.coredump import (
    CoredumpState, parse_coredump, read_coredump,
    format_hex_dump, format_stack, format_ram_summary,
    format_coredump, undump_command,
    COREDUMP_MAGIC, COREDUMP_VERSION, COREDUMP_STACK_SIZE, COREDUMP_RAM_SIZE
)


class TestCoredumpConstants(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(COREDUMP_MAGIC, b"UXNC")
        self.assertEqual(COREDUMP_VERSION, 1)
        self.assertEqual(COREDUMP_STACK_SIZE, 256)
        self.assertEqual(COREDUMP_RAM_SIZE, 65536)


class TestCoredumpParsing(unittest.TestCase):
    def _create_valid_coredump(self, wst_ptr=0, rst_ptr=0, wst=None, rst=None, ram=None):
        if wst is None:
            wst = bytes(256)
        if rst is None:
            rst = bytes(256)
        if ram is None:
            ram = bytes(65536)
        
        return COREDUMP_MAGIC + bytes([COREDUMP_VERSION, wst_ptr, rst_ptr]) + wst + rst + ram

    def test_parse_valid_coredump(self):
        data = self._create_valid_coredump(wst_ptr=5, rst_ptr=10)
        state = parse_coredump(data)
        
        self.assertIsInstance(state, CoredumpState)
        self.assertEqual(state.wst_ptr, 5)
        self.assertEqual(state.rst_ptr, 10)
        self.assertEqual(len(state.wst), 256)
        self.assertEqual(len(state.rst), 256)
        self.assertEqual(len(state.ram), 65536)

    def test_parse_coredump_wrong_size(self):
        with self.assertRaises(ValueError) as ctx:
            parse_coredump(b"too_short")
        self.assertIn("wrong size", str(ctx.exception))

    def test_parse_coredump_bad_magic(self):
        data = b"BADM" + bytes([1, 0, 0]) + bytes(256 + 256 + 65536)
        with self.assertRaises(ValueError) as ctx:
            parse_coredump(data)
        self.assertIn("Bad coredump magic", str(ctx.exception))

    def test_parse_coredump_bad_version(self):
        data = COREDUMP_MAGIC + bytes([99, 0, 0]) + bytes(256 + 256 + 65536)
        with self.assertRaises(ValueError) as ctx:
            parse_coredump(data)
        self.assertIn("Unsupported coredump version", str(ctx.exception))

    def test_read_coredump_from_file(self):
        # Create a temporary coredump file
        data = self._create_valid_coredump(wst_ptr=3, rst_ptr=7)
        
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name
        
        try:
            state = read_coredump(temp_path)
            self.assertEqual(state.wst_ptr, 3)
            self.assertEqual(state.rst_ptr, 7)
        finally:
            os.unlink(temp_path)

    def test_read_coredump_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            read_coredump("/nonexistent/path")


class TestCoredumpFormatting(unittest.TestCase):
    def test_format_hex_dump(self):
        data = bytes(range(16))
        result = format_hex_dump(data, offset=0x1000)
        
        self.assertIn("00001000", result)
        self.assertIn("00 01 02", result)
        self.assertIn("|", result)  # ASCII delimiter

    def test_format_hex_dump_max_lines(self):
        data = bytes(range(256))
        result = format_hex_dump(data, max_lines=2)
        lines = result.strip().split('\n')
        self.assertEqual(len(lines), 2)

    def test_format_stack_empty(self):
        stack = bytes(256)
        result = format_stack(stack, ptr=0, name="WST")
        self.assertIn("WST", result)
        self.assertIn("(empty)", result)

    def test_format_stack_with_data(self):
        stack = bytearray(256)
        stack[0] = 0xAA
        stack[1] = 0xBB
        result = format_stack(bytes(stack), ptr=2, name="WST")
        
        self.assertIn("WST", result)
        self.assertIn("00h", result)
        self.assertIn("aa", result)
        self.assertIn("bb", result)
        self.assertIn("<-- top", result)

    def test_format_ram_summary_all_zero(self):
        ram = bytes(65536)
        result = format_ram_summary(ram)
        self.assertIn("all zero", result)

    def test_format_ram_summary_with_data(self):
        ram = bytearray(65536)
        # Add some non-zero data
        ram[0x1000:0x1005] = bytes([1, 2, 3, 4, 5])
        result = format_ram_summary(bytes(ram))
        
        self.assertIn("non-zero regions", result)
        self.assertIn("1000", result)
        self.assertIn("1004", result)

    def test_format_coredump(self):
        state = CoredumpState(
            wst_ptr=5,
            rst_ptr=3,
            wst=bytes([1, 2, 3] + [0] * 253),
            rst=bytes([4, 5, 6] + [0] * 253),
            ram=bytes(65536)
        )
        result = format_coredump(state)
        
        self.assertIn("Uxn Coredump", result)
        self.assertIn("Format version: 1", result)
        self.assertIn("Working Stack Pointer: 05h", result)
        self.assertIn("Return Stack Pointer:", result)
        self.assertIn("Zero Page", result)

    def test_format_coredump_verbose(self):
        state = CoredumpState(
            wst_ptr=1,
            rst_ptr=0,
            wst=bytes([0xFF] * 256),
            rst=bytes(256),
            ram=bytes(65536)
        )
        result = format_coredump(state, verbose=True)
        
        self.assertIn("Full RAM Dump", result)
        self.assertIn("Working Stack (full)", result)
        self.assertIn("Return Stack (full)", result)


class TestUndumpCommand(unittest.TestCase):
    def _create_valid_coredump(self):
        return COREDUMP_MAGIC + bytes([COREDUMP_VERSION, 5, 3]) + bytes(256 + 256 + 65536)

    def test_undump_command_success(self):
        data = self._create_valid_coredump()
        
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name
        
        try:
            success, output = undump_command(temp_path)
            self.assertTrue(success)
            self.assertIn("Uxn Coredump", output)
            self.assertIn("Format version: 1", output)
        finally:
            os.unlink(temp_path)

    def test_undump_command_file_not_found(self):
        success, output = undump_command("/nonexistent/file")
        self.assertFalse(success)
        self.assertIn("File not found", output)

    def test_undump_command_invalid_data(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"invalid data")
            temp_path = f.name
        
        try:
            success, output = undump_command(temp_path)
            self.assertFalse(success)
            self.assertIn("parsing coredump", output)
        finally:
            os.unlink(temp_path)


if __name__ == '__main__':
    unittest.main()
