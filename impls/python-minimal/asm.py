#!/usr/bin/env python3
"""Minimal-but-wide Uxntal assembler: opcodes+modes, labels/sublabels, all
address runes, padding, macros (incl. nesting), comments, anonymous `{ }`
labels. Does not implement assembler/pad-label (`|label` / `$label` with a
label operand) -- see README.md for the exact scope/limitations.
"""

import argparse
import sys

HEX_DIGITS = set("0123456789abcdef")

OPCODES = {
    "INC": 0x01, "POP": 0x02, "NIP": 0x03, "SWP": 0x04, "ROT": 0x05,
    "DUP": 0x06, "OVR": 0x07, "EQU": 0x08, "NEQ": 0x09, "GTH": 0x0a,
    "LTH": 0x0b, "JMP": 0x0c, "JCN": 0x0d, "JSR": 0x0e, "STH": 0x0f,
    "LDZ": 0x10, "STZ": 0x11, "LDR": 0x12, "STR": 0x13, "LDA": 0x14,
    "STA": 0x15, "DEI": 0x16, "DEO": 0x17, "ADD": 0x18, "SUB": 0x19,
    "MUL": 0x1a, "DIV": 0x1b, "AND": 0x1c, "ORA": 0x1d, "EOR": 0x1e,
    "SFT": 0x1f,
}

IMMEDIATES = {
    "BRK": 0x00, "JCI": 0x20, "JMI": 0x40, "JSI": 0x60,
    "LIT": 0x80, "LIT2": 0xa0, "LITr": 0xc0, "LIT2r": 0xe0,
}

ADDRESS_RUNES = set(",.;_-=!?")


class AssemblerError(Exception):
    pass


def is_numeric_token(tok: str) -> bool:
    return len(tok) in (2, 4) and all(c in HEX_DIGITS for c in tok)


def parse_hex(tok: str) -> int:
    return int(tok, 16)


def signed8_range_ok(offset: int) -> bool:
    return -128 <= offset <= 127


def signed16_range_ok(offset: int) -> bool:
    return -32768 <= offset <= 32767


def tokenize(source: str) -> list:
    raw = source.split()
    tokens = []
    depth = 0
    for tok in raw:
        if tok == "(":
            depth += 1
            continue
        if tok == ")":
            depth -= 1
            continue
        if depth > 0:
            continue
        if tok in ("[", "]"):
            continue
        tokens.append(tok)
    return tokens


class Reference:
    def __init__(self, kind, name, patch_addr):
        self.kind = kind  # 'rel8', 'zp8', 'abs16', 'raw_rel8', 'raw_zp8', 'raw_abs16', 'rel16'
        self.name = name
        self.patch_addr = patch_addr


class Assembler:
    def __init__(self):
        self.output = bytearray(0x10000)
        self.highest_written = 0x00ff
        self.pc = 0x0100
        self.labels = {}
        self.macros = {}
        self.references = []
        self.scope = ""
        self.anon_stack = []
        self.anon_counter = 0
        self.expanding = set()

    def poke(self, addr: int, value: int) -> None:
        # A trailing run of zero bytes with nothing nonzero after it is not
        # part of the final ROM (matches uxnasm: length only advances on a
        # nonzero write), so the "highest written" boundary only moves for
        # nonzero values.
        value &= 0xff
        self.output[addr & 0xffff] = value
        if value != 0 and addr > self.highest_written:
            self.highest_written = addr

    def emit_byte(self, value: int) -> None:
        self.poke(self.pc, value)
        self.pc = (self.pc + 1) & 0xffff

    def reserve(self, n: int) -> int:
        # Placeholder bytes must be nonzero so they always count toward
        # "highest written" even if the final patched value (in
        # _resolve_references) turns out to be zero -- matches uxnasm.
        addr = self.pc
        for _ in range(n):
            self.emit_byte(0xff)
        return addr

    def resolve_name(self, raw_name: str) -> str:
        if raw_name.startswith("&"):
            return f"{self.scope}/{raw_name[1:]}"
        return raw_name

    def define_label(self, name: str) -> None:
        base = name.rsplit("/", 1)[-1]
        if is_numeric_token(base):
            raise AssemblerError(f"Label invalid: {name}")
        if name in self.labels:
            raise AssemblerError(f"Label duplicate: {name}")
        self.labels[name] = self.pc

    def next_anon_name(self) -> str:
        self.anon_counter += 1
        return f"__anon{self.anon_counter}"

    def _parse_pad_operand(self, operand: str) -> int:
        # assembler/pad-label (`|label` / `$label` with a label operand) is
        # not implemented -- only raw hex padding operands are supported.
        try:
            return parse_hex(operand)
        except ValueError:
            raise AssemblerError(f"Padding invalid: {operand}")

    def assemble(self, source: str) -> None:
        tokens = tokenize(source)
        queue = list(tokens)
        queue.reverse()  # use as a stack: pop from the end

        def next_token():
            return queue.pop() if queue else None

        def push_tokens(toks):
            queue.extend(reversed(toks))

        def collect_braced_body():
            opener = next_token()
            if opener != "{":
                raise AssemblerError("Macro definition missing '{'")
            depth = 1
            body = []
            while True:
                tok = next_token()
                if tok is None:
                    raise AssemblerError("Unterminated macro body")
                if tok == "{":
                    depth += 1
                elif tok == "}":
                    depth -= 1
                    if depth == 0:
                        return body
                body.append(tok)

        while True:
            tok = next_token()
            if tok is None:
                break
            if isinstance(tok, tuple):  # macro-expansion-end sentinel
                self.expanding.discard(tok[1])
                continue
            self._process_token(tok, next_token, push_tokens, collect_braced_body)

        self._resolve_references()

    def _process_token(self, tok, next_token, push_tokens, collect_braced_body):
        c = tok[0]

        if c == "|":
            self.pc = self._parse_pad_operand(tok[1:])
            return
        if c == "$":
            self.pc = (self.pc + self._parse_pad_operand(tok[1:])) & 0xffff
            return
        if c == "@":
            name = tok[1:]
            self.define_label(name)
            self.scope = name.split("/", 1)[0]
            return
        if c == "&":
            name = self.resolve_name(tok)
            self.define_label(name)
            return
        if tok == "}":
            if not self.anon_stack:
                raise AssemblerError("Unmatched '}'")
            name = self.anon_stack.pop()
            self.labels[name] = self.pc
            return
        if c == "%":
            name = tok[1:]
            if name in self.macros:
                raise AssemblerError(f"Macro duplicate: {name}")
            body = collect_braced_body()
            self.macros[name] = body
            return
        if c == '"':
            for ch in tok[1:]:
                self.emit_byte(ord(ch))
            return
        if c == "#":
            rest = tok[1:]
            if len(rest) == 2:
                self.emit_byte(IMMEDIATES["LIT"])
                self.emit_byte(parse_hex(rest))
            elif len(rest) == 4:
                self.emit_byte(IMMEDIATES["LIT2"])
                self.emit_byte(parse_hex(rest[0:2]))
                self.emit_byte(parse_hex(rest[2:4]))
            else:
                raise AssemblerError(f"Invalid literal: {tok}")
            return
        if c in ADDRESS_RUNES:
            self._process_address_rune(c, tok[1:])
            return
        if tok == "{":
            name = self.next_anon_name()
            self.anon_stack.append(name)
            self.emit_byte(IMMEDIATES["JSI"])
            self._emit_reference("rel16", name)
            return

        if tok in IMMEDIATES:
            self.emit_byte(IMMEDIATES[tok])
            return
        if is_opcode_token(tok):
            self.emit_byte(encode_opcode(tok))
            return
        if is_numeric_token(tok):
            for i in range(0, len(tok), 2):
                self.emit_byte(parse_hex(tok[i:i + 2]))
            return
        if tok in self.macros:
            if tok in self.expanding:
                raise AssemblerError(f"Macro circular: {tok}")
            self.expanding.add(tok)
            push_tokens(self.macros[tok] + [("__macro_end__", tok)])
            return

        # bare label -> JSI call
        self.emit_byte(IMMEDIATES["JSI"])
        self._emit_reference("rel16", self.resolve_name(tok))

    def _process_address_rune(self, rune: str, name_part: str) -> None:
        if name_part == "{":
            name = self.next_anon_name()
            self.anon_stack.append(name)
        else:
            name = self.resolve_name(name_part)

        if rune == ",":
            self.emit_byte(IMMEDIATES["LIT"])
            self._emit_reference("rel8", name)
        elif rune == ".":
            self.emit_byte(IMMEDIATES["LIT"])
            self._emit_reference("zp8", name)
        elif rune == ";":
            self.emit_byte(IMMEDIATES["LIT2"])
            self._emit_reference("abs16", name)
        elif rune == "_":
            self._emit_reference("raw_rel8", name)
        elif rune == "-":
            self._emit_reference("raw_zp8", name)
        elif rune == "=":
            self._emit_reference("raw_abs16", name)
        elif rune == "!":
            self.emit_byte(IMMEDIATES["JMI"])
            self._emit_reference("rel16", name)
        elif rune == "?":
            self.emit_byte(IMMEDIATES["JCI"])
            self._emit_reference("rel16", name)

    def _emit_reference(self, kind: str, name: str) -> None:
        width = 2 if kind in ("abs16", "raw_abs16", "rel16") else 1
        patch_addr = self.reserve(width)
        self.references.append(Reference(kind, name, patch_addr))

    def _patch(self, addr: int, value: int) -> None:
        # Plain overwrite, no "highest written" bookkeeping: the placeholder
        # reserved for this address during pass 1 was already nonzero, so it
        # was already counted, regardless of what the final value is here.
        self.output[addr & 0xffff] = value & 0xff

    def _resolve_references(self) -> None:
        for ref in self.references:
            if ref.name not in self.labels:
                raise AssemblerError(f"Label unknown: {ref.name}")
            target = self.labels[ref.name]

            if ref.kind in ("zp8", "raw_zp8"):
                self._patch(ref.patch_addr, target)
            elif ref.kind in ("abs16", "raw_abs16"):
                self._patch(ref.patch_addr, target >> 8)
                self._patch(ref.patch_addr + 1, target)
            elif ref.kind in ("rel8", "raw_rel8"):
                # Offset is relative to 2 bytes past the patch address: the
                # operand byte itself, plus the consuming opcode that always
                # immediately follows it (matches uxnasm's resolve()).
                offset = target - ref.patch_addr - 2
                if not signed8_range_ok(offset):
                    raise AssemblerError(f"Reference too far: {ref.name} (offset {offset})")
                self._patch(ref.patch_addr, offset)
            elif ref.kind == "rel16":
                offset = target - ref.patch_addr - 2
                if not signed16_range_ok(offset):
                    raise AssemblerError(f"Reference too far: {ref.name} (offset {offset})")
                self._patch(ref.patch_addr, offset >> 8)
                self._patch(ref.patch_addr + 1, offset)

    def rom_bytes(self) -> bytes:
        if self.highest_written < 0x0100:
            return b""
        return bytes(self.output[0x0100:self.highest_written + 1])


def is_opcode_token(tok: str) -> bool:
    if len(tok) < 3:
        return False
    name, tail = tok[:3], tok[3:]
    if name not in OPCODES:
        return False
    return all(ch in "2kr" for ch in tail)


def encode_opcode(tok: str) -> int:
    name, tail = tok[:3], tok[3:]
    value = OPCODES[name]
    if "2" in tail:
        value |= 0x20
    if "k" in tail:
        value |= 0x80
    if "r" in tail:
        value |= 0x40
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal-but-wide Uxntal assembler.")
    parser.add_argument("input", help="Path to the .tal source file")
    parser.add_argument("output", help="Path to write the assembled .rom file")
    args = parser.parse_args()

    with open(args.input, "r") as f:
        source = f.read()

    asm = Assembler()
    try:
        asm.assemble(source)
    except AssemblerError as e:
        print(str(e), file=sys.stderr)
        return 1

    with open(args.output, "wb") as f:
        f.write(asm.rom_bytes())

    return 0


if __name__ == "__main__":
    sys.exit(main())
