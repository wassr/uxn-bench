"""Coredump parsing and display utilities for Uxn coredump format v1.

The coredump format is defined in coredump_design.md:
- Magic Header: b"UXNC" (4 bytes)
- Version: 1 byte
- WST Pointer: 1 byte
- RST Pointer: 1 byte
- Working Stack Data: 256 bytes
- Return Stack Data: 256 bytes
- RAM: 65536 bytes
"""

from dataclasses import dataclass
from typing import Optional, Tuple

COREDUMP_MAGIC = b"UXNC"
COREDUMP_VERSION = 1
COREDUMP_STACK_SIZE = 256
COREDUMP_RAM_SIZE = 65536
COREDUMP_TOTAL_SIZE = 4 + 1 + 1 + 1 + 2 * COREDUMP_STACK_SIZE + COREDUMP_RAM_SIZE


@dataclass
class CoredumpState:
    """Represents the parsed state from a coredump file."""
    wst_ptr: int
    rst_ptr: int
    wst: bytes
    rst: bytes
    ram: bytes
    
    @property
    def version(self) -> int:
        return COREDUMP_VERSION


def parse_coredump(data: bytes) -> CoredumpState:
    """Parse a coredump binary file and return the CPU state.
    
    Args:
        data: The raw bytes of the coredump file
        
    Returns:
        A CoredumpState object containing the parsed state
        
    Raises:
        ValueError: If the coredump has invalid magic, version, or size
    """
    header_size = 4 + 1 + 1 + 1  # magic + version + wst_ptr + rst_ptr
    expected_size = header_size + 2 * COREDUMP_STACK_SIZE + COREDUMP_RAM_SIZE
    
    if len(data) != expected_size:
        raise ValueError(
            f"Coredump has wrong size: expected {expected_size} bytes, got {len(data)} bytes"
        )
    
    if data[0:4] != COREDUMP_MAGIC:
        raise ValueError(f"Bad coredump magic: {data[0:4]!r}, expected {COREDUMP_MAGIC!r}")
    
    version = data[4]
    if version != COREDUMP_VERSION:
        raise ValueError(f"Unsupported coredump version: {version}, expected {COREDUMP_VERSION}")
    
    offset = 7
    wst = data[offset:offset + COREDUMP_STACK_SIZE]
    offset += COREDUMP_STACK_SIZE
    rst = data[offset:offset + COREDUMP_STACK_SIZE]
    offset += COREDUMP_STACK_SIZE
    ram = data[offset:offset + COREDUMP_RAM_SIZE]
    
    return CoredumpState(
        wst_ptr=data[5],
        rst_ptr=data[6],
        wst=wst,
        rst=rst,
        ram=ram,
    )


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
    
    # Show stack contents as a circular buffer
    # Display in two parts: from ptr to end, then from start to ptr-1
    if ptr == 0:
        # Empty stack
        lines.append("    (empty)")
    else:
        # Show stack contents in order (top is at ptr-1)
        # Display up to 16 entries
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
    
    # Find non-zero regions
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
        for start, end, size in non_zero_regions[:10]:  # Show first 10 regions
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
    
    # Header
    lines.append("=" * 60)
    lines.append("Uxn Coredump")
    lines.append("=" * 60)
    lines.append("")
    
    # Basic info
    lines.append(f"Format version: {state.version}")
    lines.append(f"Working Stack Pointer: {state.wst_ptr:02x}h ({state.wst_ptr:3d})")
    lines.append(f"Return Stack Pointer:  {state.rst_ptr:02x}h ({state.rst_ptr:3d})")
    lines.append("")
    
    # Stacks
    lines.append(format_stack(state.wst, state.wst_ptr, "Working Stack (WST)"))
    lines.append("")
    lines.append(format_stack(state.rst, state.rst_ptr, "Return Stack (RST)"))
    lines.append("")
    
    # RAM summary
    lines.append(format_ram_summary(state.ram))
    lines.append("")
    
    # Zero page (always interesting)
    lines.append("Zero Page (0000h-00FFh):")
    zero_page = state.ram[0:256]
    lines.append(format_hex_dump(zero_page, offset=0, max_lines=16))
    lines.append("")
    
    if verbose:
        # Full RAM dump (if verbose)
        lines.append("Full RAM Dump:")
        lines.append(format_hex_dump(state.ram, offset=0))
        lines.append("")
        
        # Full stack dumps
        lines.append("Working Stack (full):")
        lines.append(format_hex_dump(state.wst, offset=0, max_lines=16))
        lines.append("")
        lines.append("Return Stack (full):")
        lines.append(format_hex_dump(state.rst, offset=0, max_lines=16))
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
