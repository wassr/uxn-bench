import unittest
import tempfile
import os

from buxn.coredump import (
    CoredumpState, parse_coredump, read_coredump,
    format_hex_dump, format_stack, format_ram_summary,
    format_coredump, undump_command,
    COREDUMP_MAGIC, COREDUMP_VERSION, COREDUMP_STACK_SIZE, COREDUMP_RAM_SIZE,
    FLAG_WST, FLAG_RST, FLAG_DEV, FLAG_RAM, FLAG_CYCLES, FLAG_BANKS
)


class TestCoredumpConstants(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(COREDUMP_MAGIC, b"UXNC")
        self.assertEqual(COREDUMP_VERSION, 2)
        self.assertEqual(COREDUMP_STACK_SIZE, 256)
        self.assertEqual(COREDUMP_RAM_SIZE, 65536)


class TestCoredumpParsing(unittest.TestCase):
    def _create_v1_coredump(self, wst_ptr=0, rst_ptr=0, wst=None, rst=None, ram=None):
        if wst is None:
            wst = bytes(256)
        if rst is None:
            rst = bytes(256)
        if ram is None:
            ram = bytes(65536)
        return COREDUMP_MAGIC + bytes([1, wst_ptr, rst_ptr]) + wst + rst + ram

    def _create_v2_coredump(
        self, wst_ptr=0, rst_ptr=0, halt_reason=0, exit_code=0, pc=0x0100,
        bank_count=0, flags=FLAG_WST | FLAG_RST | FLAG_DEV | FLAG_RAM | FLAG_CYCLES,
        wst=None, rst=None, dev=None, ram=None, cycles=100, banks=None
    ):
        header = bytearray(16)
        header[0:4] = COREDUMP_MAGIC
        header[4] = 2
        header[5] = wst_ptr
        header[6] = rst_ptr
        header[7] = halt_reason
        header[8] = exit_code
        header[9:11] = pc.to_bytes(2, "big")
        header[11] = bank_count
        header[12:14] = flags.to_bytes(2, "big")

        body = bytearray()
        if flags & FLAG_WST:
            body.extend(wst if wst is not None else bytes(256))
        if flags & FLAG_RST:
            body.extend(rst if rst is not None else bytes(256))
        if flags & FLAG_DEV:
            body.extend(dev if dev is not None else bytes(256))
        if flags & FLAG_RAM:
            body.extend(ram if ram is not None else bytes(65536))
        if flags & FLAG_CYCLES:
            body.extend(cycles.to_bytes(8, "big"))
        if flags & FLAG_BANKS:
            body.extend(banks if banks is not None else bytes(bank_count * 65536))

        return bytes(header + body)

    def test_parse_valid_v1_coredump(self):
        data = self._create_v1_coredump(wst_ptr=5, rst_ptr=10)
        state = parse_coredump(data)
        
        self.assertIsInstance(state, CoredumpState)
        self.assertEqual(state.version, 1)
        self.assertEqual(state.wst_ptr, 5)
        self.assertEqual(state.rst_ptr, 10)
        self.assertEqual(len(state.wst), 256)
        self.assertEqual(len(state.rst), 256)
        self.assertEqual(len(state.ram), 65536)

    def test_parse_valid_v2_coredump(self):
        data = self._create_v2_coredump(
            wst_ptr=5, rst_ptr=10, halt_reason=1, exit_code=0, pc=0x0150, cycles=42
        )
        state = parse_coredump(data)
        
        self.assertIsInstance(state, CoredumpState)
        self.assertEqual(state.version, 2)
        self.assertEqual(state.wst_ptr, 5)
        self.assertEqual(state.rst_ptr, 10)
        self.assertEqual(state.halt_reason, 1)
        self.assertEqual(state.exit_code, 0)
        self.assertEqual(state.pc, 0x0150)
        self.assertEqual(state.cycles, 42)
        self.assertIsNotNone(state.dev)
        self.assertEqual(len(state.dev), 256)
        self.assertEqual(len(state.ram), 65536)

    def test_parse_v2_coredump_missing_optional_sections(self):
        # Flags only WST and RAM
        data = self._create_v2_coredump(flags=FLAG_WST | FLAG_RAM)
        state = parse_coredump(data)
        
        self.assertEqual(state.version, 2)
        self.assertIsNone(state.dev)
        self.assertIsNone(state.cycles)
        self.assertEqual(len(state.wst), 256)
        self.assertEqual(len(state.ram), 65536)

    def test_parse_coredump_wrong_size(self):
        data = COREDUMP_MAGIC + bytes([2]) + bytes(10)
        with self.assertRaises(ValueError) as ctx:
            parse_coredump(data)
        self.assertIn("truncated", str(ctx.exception))

    def test_parse_coredump_bad_magic(self):
        data = b"BADM" + bytes(20)
        with self.assertRaises(ValueError) as ctx:
            parse_coredump(data)
        self.assertIn("Bad coredump magic", str(ctx.exception))

    def test_parse_coredump_bad_version(self):
        data = COREDUMP_MAGIC + bytes([99, 0, 0]) + bytes(20)
        with self.assertRaises(ValueError) as ctx:
            parse_coredump(data)
        self.assertIn("Unsupported coredump version", str(ctx.exception))

    def test_read_coredump_from_file(self):
        data = self._create_v2_coredump(wst_ptr=3, rst_ptr=7)
        
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
        self.assertIn("|", result)

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
        ram[0x1000:0x1005] = bytes([1, 2, 3, 4, 5])
        result = format_ram_summary(bytes(ram))
        
        self.assertIn("non-zero regions", result)
        self.assertIn("1000", result)
        self.assertIn("1004", result)

    def test_format_coredump_v2(self):
        state = CoredumpState(
            version=2,
            wst_ptr=5,
            rst_ptr=3,
            pc=0x0120,
            halt_reason=0,
            exit_code=0,
            cycles=15,
            wst=bytes([1, 2, 3] + [0] * 253),
            rst=bytes([4, 5, 6] + [0] * 253),
            dev=bytes(256),
            ram=bytes(65536)
        )
        result = format_coredump(state)
        
        self.assertIn("Uxn Coredump", result)
        self.assertIn("Format version: 2", result)
        self.assertIn("Program Counter (PC):  0120h", result)
        self.assertIn("Halt Reason:           0 (BRK instruction)", result)
        self.assertIn("Cycles Executed:       15", result)
        self.assertIn("Working Stack Pointer: 05h", result)
        self.assertIn("Return Stack Pointer:", result)
        self.assertIn("Device I/O Page", result)
        self.assertIn("Zero Page", result)

    def test_format_coredump_verbose(self):
        state = CoredumpState(
            version=2,
            wst_ptr=1,
            rst_ptr=0,
            wst=bytes([0xFF] * 256),
            rst=bytes(256),
            dev=bytes(256),
            ram=bytes(65536)
        )
        result = format_coredump(state, verbose=True)
        
        self.assertIn("Full RAM Dump", result)
        self.assertIn("Working Stack (full)", result)
        self.assertIn("Return Stack (full)", result)
        self.assertIn("Device I/O Page (full)", result)


class TestUndumpCommand(unittest.TestCase):
    def _create_valid_coredump(self):
        header = bytearray(16)
        header[0:4] = COREDUMP_MAGIC
        header[4] = 2
        header[5] = 5
        header[6] = 3
        header[12:14] = (FLAG_WST | FLAG_RST | FLAG_RAM).to_bytes(2, "big")
        return bytes(header) + bytes(256 + 256 + 65536)

    def test_undump_command_success(self):
        data = self._create_valid_coredump()
        
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(data)
            temp_path = f.name
        
        try:
            success, output = undump_command(temp_path)
            self.assertTrue(success)
            self.assertIn("Uxn Coredump", output)
            self.assertIn("Format version: 2", output)
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

