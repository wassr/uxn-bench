"""Varvara-headless device page: System (halt request + stack pointers) and
Console (stdout/stderr) only. No Screen/Audio/Controller/Mouse/File/Datetime,
and no event-vector dispatch (e.g. per-byte Console stdin vectoring) -- see
README.md for the rationale and the tests this rules out.
"""

import sys

SYSTEM_WST_LEGACY = 0x02
SYSTEM_RST_LEGACY = 0x03
SYSTEM_WST_MODERN = 0x04
SYSTEM_RST_MODERN = 0x05
SYSTEM_STATE = 0x0f
CONSOLE_WRITE = 0x18
CONSOLE_ERROR = 0x19


class Devices:
    def __init__(self, wst, rst):
        self.wst = wst
        self.rst = rst
        self.page = bytearray(256)
        self.halted = False
        self.exit_code = 0

    def dei(self, port: int) -> int:
        if port in (SYSTEM_WST_LEGACY, SYSTEM_WST_MODERN):
            return self.wst.ptr
        if port in (SYSTEM_RST_LEGACY, SYSTEM_RST_MODERN):
            return self.rst.ptr
        return self.page[port]

    def deo(self, port: int, value: int) -> None:
        value &= 0xff
        self.page[port] = value
        if port in (SYSTEM_WST_LEGACY, SYSTEM_WST_MODERN):
            self.wst.ptr = value
        elif port in (SYSTEM_RST_LEGACY, SYSTEM_RST_MODERN):
            self.rst.ptr = value
        elif port == SYSTEM_STATE and value != 0:
            self.halted = True
            self.exit_code = 0 if value == 0x80 else (value & 0x7f)
        elif port == CONSOLE_WRITE:
            sys.stdout.buffer.write(bytes([value]))
            sys.stdout.buffer.flush()
        elif port == CONSOLE_ERROR:
            sys.stderr.buffer.write(bytes([value]))
            sys.stderr.buffer.flush()
