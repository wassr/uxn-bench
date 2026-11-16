"""Coredump parsing and display utilities for Uxn coredump format v2.

The coredump format is defined in coredump_design.md:
- Header (16 bytes): Magic (4B), Version (1B), WST Ptr (1B), RST Ptr (1B), Halt Reason (1B),
  Exit Code (1B), PC (2B BE), Bank Count (1B), Presence Flags (2B BE), Reserved (2B)
- Data sections (ordered by presence flag bits): WST (256B), RST (256B), DEV (256B), RAM (64KB),
  Cycles (8B BE), Extended Banks (Bank Count * 64KB).
"""

from dataclasses import dataclass
from typing import Optional, Tuple

COREDUMP_MAGIC = b"UXNC"
COREDUMP_VERSION = 2
COREDUMP_STACK_SIZE = 256
COREDUMP_RAM_SIZE = 65536
COREDUMP_DEV_SIZE = 256

# Section Presence Flags
FLAG_WST = 0x0001
FLAG_RST = 0x0002
FLAG_DEV = 0x0004
FLAG_RAM = 0x0008
FLAG_CYCLES = 0x0010
FLAG_BANKS = 0x0020

HALT_REASONS = {
    0: "BRK instruction",
    1: "System Exit (DEO 0f)",
    2: "Timeout / Cycle limit",
    3: "WST Overflow",
    4: "WST Underflow",
    5: "RST Overflow",
    6: "RST Underflow",
    7: "CPU Trap / Invalid Instruction",
}


@dataclass
class CoredumpState:
    """Represents the parsed state from a coredump file."""
    wst_ptr: int
    rst_ptr: int
    wst: bytes
    rst: bytes
    ram: bytes
    version: int = COREDUMP_VERSION
    halt_reason: int = 0
    exit_code: int = 0
    pc: int = 0x0100
    bank_count: int = 0
    presence_flags: int = FLAG_WST | FLAG_RST | FLAG_RAM
    cycles: Optional[int] = None
    dev: Optional[bytes] = None
    banks: Optional[bytes] = None


def parse_coredump(data: bytes) -> CoredumpState:
    """Parse a coredump binary file and return the CPU state.
    
    Args:
        data: The raw bytes of the coredump file
        
    Returns:
        A CoredumpState object containing the parsed state
        
    Raises:
        ValueError: If the coredump has invalid magic, version, or size
    """
    if len(data) < 5:
        raise ValueError(f"Coredump has wrong size: file too short ({len(data)} bytes)")
    
    if data[0:4] != COREDUMP_MAGIC:
        raise ValueError(f"Bad coredump magic: {data[0:4]!r}, expected {COREDUMP_MAGIC!r}")
    
    version = data[4]
    
    if version == 1:
        expected_size = 4 + 1 + 1 + 1 + 2 * COREDUMP_STACK_SIZE + COREDUMP_RAM_SIZE
        if len(data) != expected_size:
            raise ValueError(
                f"Coredump has wrong size: expected {expected_size} bytes, got {len(data)} bytes"
            )
        
        offset = 7
        wst = data[offset:offset + COREDUMP_STACK_SIZE]
        offset += COREDUMP_STACK_SIZE
        rst = data[offset:offset + COREDUMP_STACK_SIZE]
        offset += COREDUMP_STACK_SIZE
        ram = data[offset:offset + COREDUMP_RAM_SIZE]
        
        return CoredumpState(
            version=1,
            wst_ptr=data[5],
            rst_ptr=data[6],
            wst=wst,
            rst=rst,
            ram=ram,
            presence_flags=FLAG_WST | FLAG_RST | FLAG_RAM,
        )
    
    elif version == 2:
        if len(data) < 16:
            raise ValueError(f"Coredump header truncated: expected at least 16 bytes, got {len(data)}")
        
        wst_ptr = data[5]
        rst_ptr = data[6]
        halt_reason = data[7]
        exit_code = data[8]
        pc = int.from_bytes(data[9:11], "big")
        bank_count = data[11]
        presence_flags = int.from_bytes(data[12:14], "big")
        
        offset = 16
        wst = bytes(COREDUMP_STACK_SIZE)
        rst = bytes(COREDUMP_STACK_SIZE)
        dev = None
        ram = bytes(COREDUMP_RAM_SIZE)
        cycles = None
        banks = None
        
        if presence_flags & FLAG_WST:
            if offset + COREDUMP_STACK_SIZE > len(data):
                raise ValueError("Coredump truncated reading WST")
            wst = data[offset:offset + COREDUMP_STACK_SIZE]
            offset += COREDUMP_STACK_SIZE
            
        if presence_flags & FLAG_RST:
            if offset + COREDUMP_STACK_SIZE > len(data):
                raise ValueError("Coredump truncated reading RST")
            rst = data[offset:offset + COREDUMP_STACK_SIZE]
            offset += COREDUMP_STACK_SIZE
            
        if presence_flags & FLAG_DEV:
            if offset + COREDUMP_DEV_SIZE > len(data):
                raise ValueError("Coredump truncated reading DEV")
            dev = data[offset:offset + COREDUMP_DEV_SIZE]
            offset += COREDUMP_DEV_SIZE
            
        if presence_flags & FLAG_RAM:
            if offset + COREDUMP_RAM_SIZE > len(data):
                raise ValueError("Coredump truncated reading RAM")
            ram = data[offset:offset + COREDUMP_RAM_SIZE]
            offset += COREDUMP_RAM_SIZE
            
        if presence_flags & FLAG_CYCLES:
            if offset + 8 > len(data):
                raise ValueError("Coredump truncated reading CYCLES")
            cycles = int.from_bytes(data[offset:offset + 8], "big")
            offset += 8
            
        if presence_flags & FLAG_BANKS:
            bank_bytes = bank_count * COREDUMP_RAM_SIZE
            if offset + bank_bytes > len(data):
                raise ValueError("Coredump truncated reading BANKS")
            banks = data[offset:offset + bank_bytes]
            offset += bank_bytes
            
        if offset != len(data):
            raise ValueError(
                f"Coredump has wrong size: expected {offset} bytes, got {len(data)} bytes"
            )
            
        return CoredumpState(
            version=2,
            wst_ptr=wst_ptr,
            rst_ptr=rst_ptr,
            halt_reason=halt_reason,
            exit_code=exit_code,
            pc=pc,
            bank_count=bank_count,
            presence_flags=presence_flags,
            cycles=cycles,
            wst=wst,
            rst=rst,
            dev=dev,
            ram=ram,
            banks=banks,
        )
    
    else:
        raise ValueError(f"Unsupported coredump version: {version}")


def read_coredump(filepath: str) -> CoredumpState:
    """Read and parse a coredump file from disk.
    
    Args:
        filepath: Path to the coredump file
        
    Returns:
        A CoredumpState object
        
    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file is not a valid coredump
    """
    with open(filepath, "rb") as f:
        data = f.read()
    return parse_coredump(data)


def format_hex_dump(data: bytes, offset: int = 0, max_lines: Optional[int] = None) -> str:
    """Format bytes as a hex dump with ASCII representation.
    
    Args:
        data: Bytes to format
        offset: Starting address for the dump
        max_lines: Maximum number of lines to display (None for all)
        
    Returns:
        Formatted hex dump string
    """
    lines = []
    addr_width = max(8, len(f"{offset + len(data) - 1:04x}"))
    
    for i in range(0, len(data), 16):
        if max_lines is not None and len(lines) >= max_lines:
            break
        
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        addr = offset + i
        
        lines.append(f"  {addr:0{addr_width}x}  {hex_part:<48s}  |{ascii_part}|")
    
    return "\n".join(lines)


def format_stack(stack: bytes, ptr: int, name: str) -> str:
    """Format a stack with its pointer for display.
    
    Args:
        stack: The stack data (256 bytes)
        ptr: Current stack pointer (0-255)
        name: Stack name (e.g., "WST", "RST")
        
    Returns:
        Formatted string showing stack state
    """
    lines = []
    lines.append(f"{name} (ptr={ptr:02x}h / {ptr:3d}):")
    
    if ptr == 0:
        lines.append("    (empty)")
    else:
        for i in range(max(0, ptr - 16), ptr):
            idx = i & 0xFF
            val = stack[idx]
            marker = " <-- top" if idx == ((ptr - 1) & 0xFF) else ""
            lines.append(f"    [{idx:02x}h] = {val:02x}h{marker}")
    
    return "\n".join(lines)


def format_ram_summary(ram: bytes) -> str:
    """Format a summary of RAM contents.
    
    Args:
        ram: The full RAM dump (65536 bytes)
        
    Returns:
        Formatted string with RAM summary
    """
    lines = []
    
    non_zero_regions = []
    i = 0
    while i < len(ram):
        if ram[i] != 0:
            start = i
            while i < len(ram) and ram[i] != 0:
                i += 1
            end = i - 1
            size = end - start + 1
            non_zero_regions.append((start, end, size))
        else:
            i += 1
    
    if non_zero_regions:
        lines.append("RAM non-zero regions:")
        for start, end, size in non_zero_regions[:10]:
            lines.append(f"  {start:04x}h - {end:04x}h ({size:5d} bytes)")
        if len(non_zero_regions) > 10:
            lines.append(f"  ... and {len(non_zero_regions) - 10} more regions")
    else:
        lines.append("RAM: all zero")
    
    return "\n".join(lines)


def format_coredump(state: CoredumpState, verbose: bool = False) -> str:
    """Format a coredump state for human-readable display.
    
    Args:
        state: The parsed coredump state
        verbose: If True, include full hex dumps
        
    Returns:
        Formatted string representation of the coredump
    """
    lines = []
    
    lines.append("=" * 60)
    lines.append("Uxn Coredump")
    lines.append("=" * 60)
    lines.append("")
    
    lines.append(f"Format version: {state.version}")
    if state.version >= 2:
        reason_str = HALT_REASONS.get(state.halt_reason, f"Unknown ({state.halt_reason})")
        lines.append(f"Program Counter (PC):  {state.pc:04x}h ({state.pc:5d})")
        lines.append(f"Halt Reason:           {state.halt_reason} ({reason_str})")
        lines.append(f"Exit Code:             {state.exit_code:02x}h ({state.exit_code:3d})")
        lines.append(f"Cycles Executed:       {state.cycles if state.cycles is not None else 'N/A'}")
    lines.append(f"Working Stack Pointer: {state.wst_ptr:02x}h ({state.wst_ptr:3d})")
    lines.append(f"Return Stack Pointer:  {state.rst_ptr:02x}h ({state.rst_ptr:3d})")
    lines.append("")
    
    lines.append(format_stack(state.wst, state.wst_ptr, "Working Stack (WST)"))
    lines.append("")
    lines.append(format_stack(state.rst, state.rst_ptr, "Return Stack (RST)"))
    lines.append("")
    
    if state.dev is not None:
        lines.append("Device I/O Page (0000h-00FFh):")
        lines.append(format_hex_dump(state.dev, offset=0, max_lines=16))
        lines.append("")
        
    lines.append(format_ram_summary(state.ram))
    lines.append("")
    
    lines.append("Zero Page (0000h-00FFh):")
    zero_page = state.ram[0:256]
    lines.append(format_hex_dump(zero_page, offset=0, max_lines=16))
    lines.append("")
    
    if verbose:
        lines.append("Full RAM Dump:")
        lines.append(format_hex_dump(state.ram, offset=0))
        lines.append("")
        
        lines.append("Working Stack (full):")
        lines.append(format_hex_dump(state.wst, offset=0, max_lines=16))
        lines.append("")
        lines.append("Return Stack (full):")
        lines.append(format_hex_dump(state.rst, offset=0, max_lines=16))
        lines.append("")
        
        if state.dev is not None:
            lines.append("Device I/O Page (full):")
            lines.append(format_hex_dump(state.dev, offset=0, max_lines=16))
            lines.append("")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)


def undump_command(filepath: str, verbose: bool = False) -> Tuple[bool, str]:
    """Execute the undump command.
    
    Args:
        filepath: Path to the coredump file
        verbose: If True, show full hex dumps
        
    Returns:
        Tuple of (success: bool, output: str)
    """
    try:
        state = read_coredump(filepath)
        output = format_coredump(state, verbose=verbose)
        return True, output
    except FileNotFoundError:
        return False, f"Error: File not found: {filepath}"
    except ValueError as e:
        return False, f"Error parsing coredump: {e}"
    except Exception as e:
        return False, f"Error: {e}"

