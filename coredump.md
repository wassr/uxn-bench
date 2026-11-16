# Uxn Coredump Format & Extraction Design

This document addresses the design of a coredump format for Uxn tests and answers the core question: **Is it possible/practical to emulate a coredump using an injected footer with Varvara devices?**

## 1. The "Bootstrap / Catch-22" Problem

You correctly pointed out that for pure CPU tests, requiring `Console` or `File` devices to be working is a big ask. If we use an injected footer to generate the coredump:
- The CPU must already perfectly execute `LIT`, `DEO`, `DEI`, and stack operations.
- If a CPU bug exists in `DEO` or stack wrapping, the footer will fail to execute correctly, masking the bug or crashing before the dump is generated.
- Therefore, **an injected footer is fundamentally unsuitable for low-level CPU validation.**

## 2. The "Stack Clobbering" Problem

You asked: *"Can we dump the stack without needing the stack? Or do we make sure that even when testing things like stack wrapping the footer may need some stack space itself."*

**No, a Tal program cannot dump the stack without perturbing it.**
Uxn is a pure stack machine. There is no instruction to read a device or write to memory without pushing addresses/values to the stack first.
- To write to the Console, you must do `#18 DEO`. This pushes `18` to the Working Stack (WST), advancing the pointer.
- If the test ended with the stack exactly full (`ptr = 255`), pushing `#18` will wrap the stack and **overwrite** the item at index `0`. 
- Even reading the stack pointers (`#04 DEI` for WST, `#05 DEI` for RST from the System device) requires pushing to the stack.

You can dump the stacks by unrolling 256 `#18 DEO` instructions (which pops the whole stack in reverse), but this destroys the stack contents and fails to capture the exact top-of-stack without altering it first.

## 3. The Solution: Native Coredumps

Because of the Bootstrap and Stack Clobbering problems, **state extraction for CPU tests must be handled by the interpreter itself (Native Dump), not by an injected Tal footer.** 

The ideal flow is:
1. The test program runs and reaches a `#010f DEO` (or similar `BRK` / exit signal).
2. The interpreter catches this halt.
3. The interpreter natively dumps its exact memory array, WST array/pointer, and RST array/pointer to a binary file.
4. The test runner (Python) reads this binary file and compares it to the expected state.

This requires ZERO test-side logic, ZERO stack space, and works even if the CPU's device I/O or branching is completely broken.

## 4. Binary Coredump Format (v2)

Format v2 introduces a fixed 16-byte header with a section presence bitmask, metadata (Halt Reason, Exit Code, PC, Cycles), optional Device I/O page dumping, and optional Extended Memory banking support.

The binary format is uncompressed for simplicity and speed.

### Header Layout (16 Bytes)

| Offset | Size | Field | Description |
|---|---|---|---|
| 0 | 4 | Magic Header | `U` `X` `N` `C` |
| 4 | 1 | Format Version | `0x02` |
| 5 | 1 | WST Pointer | `0x00`-`0xFF` |
| 6 | 1 | RST Pointer | `0x00`-`0xFF` |
| 7 | 1 | Halt Reason | VM exit reason (`0`=BRK, `1`=System Exit, `2`=Timeout, `3`=WST Overflow, `4`=WST Underflow, `5`=RST Overflow, `6`=RST Underflow, `7`=Trap) |
| 8 | 1 | Exit Code | Exit status byte returned by System device (`0x0f`) |
| 9 | 2 | Program Counter (`pc`) | 16-bit address (Big-Endian uint16) |
| 11 | 1 | Bank Count | Number of 64KB extended memory banks |
| 12 | 2 | Presence Flags | 16-bit bitmask of present sections (Big-Endian uint16) |
| 14 | 2 | Reserved | Zero padding (aligns header to 16 bytes) |

### Section Presence Flags & File Body Order

If a bit is set in `Presence Flags`, the corresponding data bytes are appended sequentially to the file body after the 16-byte header:

1. **Bit `0x0001` (WST):** Working Stack Data (256 bytes)
2. **Bit `0x0002` (RST):** Return Stack Data (256 bytes)
3. **Bit `0x0004` (DEV):** Device I/O Page (256 bytes)
4. **Bit `0x0008` (RAM):** Main RAM (65,536 bytes)
5. **Bit `0x0010` (CYCLES):** Cycle / Step Counter (8 bytes, Big-Endian uint64)
6. **Bit `0x0020` (BANKS):** Extended Memory Banks (`Bank Count` × 65,536 bytes)

### Legacy Version 1 Format (66,055 Bytes)
For backward compatibility, v1 format remains supported by parsers:
- Header: Magic `UXNC` (4B), Version `0x01` (1B), WST Ptr (1B), RST Ptr (1B)
- Data: WST (256B), RST (256B), RAM (65,536B)

---

## Conclusion & Recommendation

1. **Abandon the injected footer approach for CPU tests.** It's theoretically compromised by stack clobbering and practically compromised by relying on the very CPU instructions it aims to test.
2. **Adopt the Binary Coredump Format (v2) above.**
3. **Modify interpreters** to accept a `--dump-on-halt <file>` flag. When the CPU halts, it writes the binary state to disk.
4. Update the Python runner to assert against this binary state.

