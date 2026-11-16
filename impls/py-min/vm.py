#!/usr/bin/env python3
"""Minimal Uxn CPU interpreter (varvara-headless: System + Console only).

Implements the 32 base opcodes (LIT/JCI/JMI/JSI immediates included) with
full wrap-around arithmetic (cpu/wrap, cpu/stack-wrap -- no traps), and an
optional binary coredump on halt for testing. Runs the reset vector once,
straight-line, to completion (BRK) -- there is no event-vector dispatch, so
e.g. per-byte Console stdin vectoring is not supported. See README.md.
"""

import argparse
import sys
from devices import Devices

import os
import atexit

_covered_lines = set()
def _trace_vm(frame, event, arg):
    if event == 'line':
        filename = frame.f_code.co_filename
        if "py-min" in filename:
            base = os.path.basename(filename)
            if base in ("vm.py", "devices.py"):
                _covered_lines.add((base, frame.f_lineno))
    return _trace_vm

if os.environ.get("BUXN_COVERAGE") == "1":
    sys.settrace(_trace_vm)
    def _save_coverage():
        sys.settrace(None)
        cov_dir = os.path.join(".buxn", "tmp", "coverage")
        os.makedirs(cov_dir, exist_ok=True)
        import uuid
        cov_file = os.path.join(cov_dir, f"{uuid.uuid4()}.cov")
        with open(cov_file, "w") as f:
            for base, line in _covered_lines:
                f.write(f"{base}:{line}\n")
        sys.stderr.write(f"COVERAGE SAVED: {len(_covered_lines)} lines\n")
    atexit.register(_save_coverage)

RAM_SIZE = 0x10000
STACK_SIZE = 0x100
MASK8 = 0xff
MASK16 = 0xffff

COREDUMP_MAGIC = b"UXNC"
COREDUMP_VERSION = 2

FLAG_WST = 0x0001
FLAG_RST = 0x0002
FLAG_DEV = 0x0004
FLAG_RAM = 0x0008
FLAG_CYCLES = 0x0010
FLAG_BANKS = 0x0020


def signed8(b: int) -> int:
    return b - 256 if b & 0x80 else b


class Stack:
    def __init__(self):
        self.data = bytearray(STACK_SIZE)
        self.ptr = 0

    def push8(self, v: int) -> None:
        self.data[self.ptr] = v & MASK8
        self.ptr = (self.ptr + 1) & MASK8

    def pop8(self) -> int:
        self.ptr = (self.ptr - 1) & MASK8
        return self.data[self.ptr]

    def peek8(self, depth: int = 0) -> int:
        return self.data[(self.ptr - 1 - depth) & MASK8]

    def push16(self, v: int) -> None:
        self.push8((v >> 8) & MASK8)
        self.push8(v & MASK8)

    def pop16(self) -> int:
        lo = self.pop8()
        hi = self.pop8()
        return (hi << 8) | lo

    def peek16(self, depth: int = 0) -> int:
        lo = self.peek8(depth * 2)
        hi = self.peek8(depth * 2 + 1)
        return (hi << 8) | lo

    def push(self, v: int, short: bool) -> None:
        self.push16(v) if short else self.push8(v)

    def pop(self, short: bool) -> int:
        return self.pop16() if short else self.pop8()

    def peek(self, depth: int, short: bool) -> int:
        return self.peek16(depth) if short else self.peek8(depth)


class Uxn:
    def __init__(self):
        self.ram = bytearray(RAM_SIZE)
        self.wst = Stack()
        self.rst = Stack()
        self.devices = Devices(self.wst, self.rst)
        self.pc = 0x0100
        self.cycles = 0
        self.halt_reason = 0  # 0=BRK, 1=System Exit

    def load_rom(self, rom: bytes) -> None:
        self.ram[0x0100:0x0100 + len(rom)] = rom

    def read_mem(self, addr: int, short: bool, zp: bool = False) -> int:
        mask = MASK8 if zp else MASK16
        if short:
            hi = self.ram[addr & mask]
            lo = self.ram[(addr + 1) & mask]
            return (hi << 8) | lo
        return self.ram[addr & mask]

    def write_mem(self, addr: int, value: int, short: bool, zp: bool = False) -> None:
        mask = MASK8 if zp else MASK16
        if short:
            self.ram[addr & mask] = (value >> 8) & MASK8
            self.ram[(addr + 1) & mask] = value & MASK8
        else:
            self.ram[addr & mask] = value & MASK8

    def dei(self, port: int, short: bool) -> int:
        if short:
            hi = self.devices.dei(port & MASK8)
            lo = self.devices.dei((port + 1) & MASK8)
            return (hi << 8) | lo
        return self.devices.dei(port & MASK8)

    def deo(self, port: int, value: int, short: bool) -> None:
        if short:
            self.devices.deo(port & MASK8, (value >> 8) & MASK8)
            self.devices.deo((port + 1) & MASK8, value & MASK8)
        else:
            self.devices.deo(port & MASK8, value & MASK8)

    def run(self) -> None:
        self.pc = 0x0100
        while True:
            if self.devices.halted:
                self.halt_reason = 1
                break
            instr = self.ram[self.pc]
            prev_pc = self.pc
            self.pc = (self.pc + 1) & MASK16
            self.cycles += 1
            if instr == 0x00:
                self.pc = prev_pc
                self.halt_reason = 0
                break
            self.pc = self._exec(instr, self.pc)
            if self.devices.halted:
                self.halt_reason = 1
                break

    def _exec(self, instr: int, pc: int) -> int:
        base = instr & 0x1f

        if base == 0x00:
            return self._exec_immediate(instr, pc)

        keep = bool(instr & 0x80)
        ret = bool(instr & 0x40)
        short = bool(instr & 0x20)
        src = self.rst if ret else self.wst
        dst = self.wst if ret else self.rst  # "other" stack, for JSR/STH
        mask = MASK16 if short else MASK8

        if base == 0x01:  # INC
            a = src.peek(0, short) if keep else src.pop(short)
            src.push((a + 1) & mask, short)
        elif base == 0x02:  # POP
            if not keep:
                src.pop(short)
        elif base == 0x06:  # DUP
            a = src.peek(0, short) if keep else src.pop(short)
            src.push(a, short)
            src.push(a, short)
        elif base == 0x03:  # NIP
            if keep:
                b = src.peek(0, short)
                src.peek(1, short)
            else:
                b = src.pop(short)
                src.pop(short)
            src.push(b, short)
        elif base == 0x04:  # SWP
            b = src.peek(0, short) if keep else src.pop(short)
            a = src.peek(1, short) if keep else src.pop(short)
            src.push(b, short)
            src.push(a, short)
        elif base == 0x07:  # OVR
            b = src.peek(0, short) if keep else src.pop(short)
            a = src.peek(1, short) if keep else src.pop(short)
            src.push(a, short)
            src.push(b, short)
            src.push(a, short)
        elif base == 0x05:  # ROT
            c = src.peek(0, short) if keep else src.pop(short)
            b = src.peek(1, short) if keep else src.pop(short)
            a = src.peek(2, short) if keep else src.pop(short)
            src.push(b, short)
            src.push(c, short)
            src.push(a, short)
        elif base in (0x08, 0x09, 0x0a, 0x0b):  # EQU, NEQ, GTH, LTH
            b = src.peek(0, short) if keep else src.pop(short)
            a = src.peek(1, short) if keep else src.pop(short)
            if base == 0x08:
                result = 1 if a == b else 0
            elif base == 0x09:
                result = 1 if a != b else 0
            elif base == 0x0a:
                result = 1 if a > b else 0
            else:
                result = 1 if a < b else 0
            src.push8(result)  # comparisons always push a byte bool
        elif base == 0x0c:  # JMP
            addr = src.peek(0, short) if keep else src.pop(short)
            pc = addr if short else (pc + signed8(addr)) & MASK16
        elif base == 0x0d:  # JCN
            if keep:
                addr = src.peek16(0) if short else src.peek8(0)
                cond = src.peek8(2 if short else 1)
            else:
                addr = src.pop16() if short else src.pop8()
                cond = src.pop8()
            if cond != 0:
                pc = addr if short else (pc + signed8(addr)) & MASK16
        elif base == 0x0e:  # JSR
            addr = src.peek(0, short) if keep else src.pop(short)
            dst.push16(pc)
            pc = addr if short else (pc + signed8(addr)) & MASK16
        elif base == 0x0f:  # STH
            a = src.peek(0, short) if keep else src.pop(short)
            dst.push(a, short)
        elif base == 0x10:  # LDZ
            addr = src.peek8(0) if keep else src.pop8()
            src.push(self.read_mem(addr, short, zp=True), short)
        elif base == 0x11:  # STZ
            addr, val = self._pop_store_operands(src, keep, short, addr_short=False)
            self.write_mem(addr, val, short, zp=True)
        elif base == 0x12:  # LDR
            addr = src.peek8(0) if keep else src.pop8()
            src.push(self.read_mem((pc + signed8(addr)) & MASK16, short), short)
        elif base == 0x13:  # STR
            addr, val = self._pop_store_operands(src, keep, short, addr_short=False)
            self.write_mem((pc + signed8(addr)) & MASK16, val, short)
        elif base == 0x14:  # LDA
            addr = src.peek16(0) if keep else src.pop16()
            src.push(self.read_mem(addr, short), short)
        elif base == 0x15:  # STA
            addr, val = self._pop_store_operands(src, keep, short, addr_short=True)
            self.write_mem(addr, val, short)
        elif base == 0x16:  # DEI
            port = src.peek8(0) if keep else src.pop8()
            src.push(self.dei(port, short), short)
        elif base == 0x17:  # DEO
            addr, val = self._pop_store_operands(src, keep, short, addr_short=False)
            self.deo(addr, val, short)
        elif base in (0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e):  # ADD,SUB,MUL,DIV,AND,ORA,EOR
            b = src.peek(0, short) if keep else src.pop(short)
            a = src.peek(1, short) if keep else src.pop(short)
            if base == 0x18:
                result = a + b
            elif base == 0x19:
                result = a - b
            elif base == 0x1a:
                result = a * b
            elif base == 0x1b:
                result = (a // b) if b != 0 else 0
            elif base == 0x1c:
                result = a & b
            elif base == 0x1d:
                result = a | b
            else:
                result = a ^ b
            src.push(result & mask, short)
        elif base == 0x1f:  # SFT
            if keep:
                shift = src.peek8(0)
                a = ((src.peek8(2) << 8) | src.peek8(1)) if short else src.peek8(1)
            else:
                shift = src.pop8()
                a = src.pop16() if short else src.pop8()
            result = (a >> (shift & 0x0f)) << ((shift >> 4) & 0x0f)
            src.push(result & mask, short)

        return pc

    def _pop_store_operands(self, src: Stack, keep: bool, short: bool, addr_short: bool):
        # Layout on stack is always "value addr" (addr on top); addr's width is
        # intrinsic to the opcode (zero-page/relative = 1 byte, absolute = 2
        # bytes), independent of the value's mode-controlled width.
        addr_width = 2 if addr_short else 1
        if keep:
            addr = src.peek16(0) if addr_short else src.peek8(0)
            if short:
                val = (src.peek8(addr_width + 1) << 8) | src.peek8(addr_width)
            else:
                val = src.peek8(addr_width)
        else:
            addr = src.pop16() if addr_short else src.pop8()
            val = src.pop16() if short else src.pop8()
        return addr, val

    def _read_offset16(self, pc: int) -> int:
        raw = self.read_mem(pc, short=True)
        return raw - 0x10000 if raw >= 0x8000 else raw

    def _exec_immediate(self, instr: int, pc: int) -> int:
        if instr == 0x20:  # JCI
            cond = self.wst.pop8()
            offset = self._read_offset16(pc)
            return (pc + 2 + offset) & MASK16 if cond != 0 else (pc + 2) & MASK16
        if instr == 0x40:  # JMI
            offset = self._read_offset16(pc)
            return (pc + 2 + offset) & MASK16
        if instr == 0x60:  # JSI
            offset = self._read_offset16(pc)
            self.rst.push16((pc + 2) & MASK16)
            return (pc + 2 + offset) & MASK16
        if instr == 0x80:  # LIT
            self.wst.push8(self.ram[pc])
            return (pc + 1) & MASK16
        if instr == 0xa0:  # LIT2
            self.wst.push16((self.ram[pc] << 8) | self.ram[(pc + 1) & MASK16])
            return (pc + 2) & MASK16
        if instr == 0xc0:  # LITr
            self.rst.push8(self.ram[pc])
            return (pc + 1) & MASK16
        if instr == 0xe0:  # LIT2r
            self.rst.push16((self.ram[pc] << 8) | self.ram[(pc + 1) & MASK16])
            return (pc + 2) & MASK16
        return pc  # unassigned op-0 byte: no-op


def write_coredump(path: str, vm: Uxn) -> None:
    flags = FLAG_WST | FLAG_RST | FLAG_DEV | FLAG_RAM | FLAG_CYCLES
    header = bytearray(16)
    header[0:4] = COREDUMP_MAGIC
    header[4] = COREDUMP_VERSION
    header[5] = vm.wst.ptr
    header[6] = vm.rst.ptr
    header[7] = vm.halt_reason
    header[8] = vm.devices.exit_code
    header[9:11] = vm.pc.to_bytes(2, "big")
    header[11] = 0  # Bank Count
    header[12:14] = flags.to_bytes(2, "big")
    header[14:16] = b"\x00\x00"

    with open(path, "wb") as f:
        f.write(header)
        f.write(vm.wst.data)
        f.write(vm.rst.data)
        f.write(vm.devices.page)
        f.write(vm.ram)
        f.write(vm.cycles.to_bytes(8, "big"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal Uxn interpreter (varvara-headless).")
    parser.add_argument("rom", help="Path to the assembled .rom file")
    parser.add_argument("--dump-on-halt", metavar="PATH", help="Write a binary coredump to PATH when the reset vector halts")
    args = parser.parse_args()

    with open(args.rom, "rb") as f:
        rom = f.read()

    vm = Uxn()
    vm.load_rom(rom)
    vm.run()

    if args.dump_on_halt:
        write_coredump(args.dump_on_halt, vm)

    return vm.devices.exit_code


if __name__ == "__main__":
    sys.exit(main())
