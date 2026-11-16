# Uxn / Uxntal / Varvara — Technical Reference

Reference for implementing interpreters, JITs, assemblers, disassemblers, debuggers,
test suites and benchmarks. Sources: wiki.xxiivv.com (uxn, uxntal, uxntal_reference,
varvara), 100r.co. Uxn is frozen (Kelvin versioning: Uxn 0K, Uxntal 54K, Varvara 80K).

## 1. Terminology (the three names)

| Name | What it is |
|---|---|
| **Uxn** | The CPU / virtual machine spec: 8-bit stack machine, 64KB memory, 256 opcodes. Defines NOTHING about I/O semantics beyond the DEI/DEO instructions. |
| **Uxntal** | The assembly language for Uxn (concatenative, postfix, hex-only) AND the name of its source format (`.tal`). "An assembler" = a program compiling `.tal` → `.rom`. Reference assemblers: `uxnasm` (C), `drifblim` (self-hosted, written in Uxntal). |
| **Varvara** | The canonical *device set* / "computer system" spec: defines what the 16 device slots mean (System, Console, Screen, Audio, Controller, Mouse, File, Datetime). Analogy from the spec: "Uxn is to Varvara what the 6502 is to the Nintendo." |
| **ROM** | Raw Uxn bytecode file, headerless. Loaded at address `0x0100`. Max size `0xff00` (65280) bytes. Trailing zero bytes may be trimmed from the file. |

An emulator = Uxn core + some device set (usually Varvara, but not necessarily; see §6).

## 2. Uxn machine model

### State
| Component | Size | Notes |
|---|---|---|
| Main memory (RAM) | 65536 bytes | Flat, byte-addressable, big-endian shorts. Code and data share it; self-modifying code is legal and used in practice. |
| Working stack (WST) | 256 bytes data + 1 byte pointer | Outside addressable memory. |
| Return stack (RST) | 256 bytes data + 1 byte pointer | Outside addressable memory. |
| Device page | 256 bytes (16 devices × 16 ports) | Outside addressable memory. Accessed only via DEI/DEO. |
| PC | 16 bits | Wraps mod 0x10000. |

### Execution model
- Boot: zero RAM, stacks, device page; load ROM at `0x0100`; begin executing at `0x0100` (the *reset vector*).
- Soft reboot: zero page (`0x0000–0x00ff`) is preserved; rest re-initialized.
- Evaluation runs until `BRK` (0x00). The host then waits for events; a device event causes the host to call into the VM at the address stored in that device's *vector* port. Each vector evaluation again runs until `BRK`.
- Vectors are **sequential and non-interruptible**: Uxn has no interrupts, no preemption, no concurrency. Stack pointers and contents persist between vector runs.
- **There are no invalid programs / no traps.** Any byte sequence executes: stack under/overflow wraps, division by zero yields zero, all arithmetic wraps mod 2^8 / 2^16. The VM never raises an error (the host may still halt via System/state or on its own).

### Stacks are circular
Pointer is an 8-bit index that wraps. Popping an "empty" stack sets the pointer to 255 and returns whatever byte is there; pushing past 255 wraps to 0. No underflow/overflow errors. Keep-mode (`k`) saves the stack pointer before the pop phase and restores it before pushing results.

### Memory edge cases (critical for interpreters/JITs)
- Shorts are **big-endian**; the address of a short is the address of its high byte.
- 16-bit accesses at the boundary **wrap**: `STA2`/`LDA2` at `0xffff` touches `M[0xffff]` (high) and `M[0x0000]` (low). Same applies to immediate-operand fetches and PC increments (PC wraps mod 0x10000).
- Zero-page 16-bit accesses wrap **within the zero page**: `STZ2`/`LDZ2` at `0xff` touches `0x00ff` and `0x0000` (low byte wraps to `0x00`).
- 16-bit device accesses (`DEI2`/`DEO2`) are performed as two byte accesses, **high byte first** (port `d`, then `d+1`, wrapping within the 256-byte device page). Some devices act only on the low-byte write.
- The zero page cannot be pre-filled by the ROM (ROM starts at 0x0100); it must be written at runtime.

## 3. Opcode set

### Encoding
One byte per opcode: bit7 = `k` (keep), bit6 = `r` (return), bit5 = `2` (short), bits 0–4 = base op (32 base ops).

- `2` short mode: operate on 16-bit values; for jumps, address on stack is an absolute short instead of a relative signed byte; for loads/stores, transfer a short.
- `k` keep mode: do not consume inputs (restore stack pointer after pops, then push results).
- `r` return mode: swap roles of WST and RST (op reads/writes RST; STHr moves RST→WST; JSRr pushes return address to WST).
- Modes combine freely: e.g. `ADD2kr` = 0xf8. Mnemonic order: `NAME` + `2` + `k` + `r` (e.g. `LDA2k` = 0xb4, `INC2r` = 0x61).

Base op `0x00` with mode bits is NOT "BRK with modes" but 8 distinct **immediate opcodes** (operands in instruction stream, not on stack):

| Byte | Mnemonic | Semantics |
|---|---|---|
| 0x00 | BRK | End current vector evaluation. No modes. |
| 0x20 | JCI (`?label`) | Pop byte from WST; if ≠0, `pc += 2 + s16 operand`, else `pc += 2`. Operand is a big-endian signed short immediately after the opcode; offset is relative to the address *after* the operand. |
| 0x40 | JMI (`!label`) | `pc += 2 + s16 operand`. |
| 0x60 | JSI (bare `label`) | Push `pc+2` (address after operand) to RST as a short, then `pc += 2 + s16 operand`. Bare label tokens assemble to JSI. |
| 0x80 / 0xa0 / 0xc0 / 0xe0 | LIT / LIT2 / LITr / LIT2r | Push next 1 or 2 bytes from program memory to WST (RST with `r`); advance PC past them. LIT is conceptually "BRK with the k-bit": the keep bit is always set on literals. |

Immediate jumps have no modes; their PC-relative encoding makes code position-independent.

### Base opcodes (hex = no-mode form; stack notation `before -- after`, top = right)
`bool8` = 01/00. Unmarked operands follow the size of the current mode.

```
01 INC  a -- a+1
02 POP  a --
03 NIP  a b -- b
04 SWP  a b -- b a
05 ROT  a b c -- b c a
06 DUP  a -- a a
07 OVR  a b -- a b a
08 EQU  a b -- bool8
09 NEQ  a b -- bool8
0a GTH  a b -- bool8          (a>b, unsigned)
0b LTH  a b -- bool8          (a<b, unsigned)
0c JMP  addr --               byte mode: pc += s8; short mode: pc = addr16
0d JCN  cond8 addr --         jump if cond8 != 0 (cond is ALWAYS one byte, even in short mode)
0e JSR  addr -- | ret16       push pc (next instr) to RST as short, then jump like JMP
0f STH  a -- | a              move WST top to RST (STHr: RST → WST)
10 LDZ  addr8 -- value        zero-page load
11 STZ  val addr8 --          zero-page store
12 LDR  addr8 -- value        PC-relative load, s8 offset (-128..+127)
13 STR  val addr8 --          PC-relative store, s8 offset
14 LDA  addr16 -- value       absolute load
15 STA  val addr16 --         absolute store
16 DEI  port8 -- value        device input (may trigger host I/O side effects)
17 DEO  val port8 --          device output (may trigger host I/O side effects)
18 ADD  a b -- a+b            wrapping
19 SUB  a b -- a-b            wrapping
1a MUL  a b -- a*b            wrapping (mod 2^8 / 2^16)
1b DIV  a b -- a/b            unsigned; b=0 → result 0; truncates toward zero
1c AND  a b -- a&b
1d ORA  a b -- a|b
1e EOR  a b -- a^b
1f SFT  a shift8 -- c         shift8 is ALWAYS one byte: low nibble = right-shift amount
                              (applied FIRST), high nibble = left-shift amount.
                              `a` follows short mode. #34 #10 SFT = 68; #34 #01 SFT = 1a.
```

Notes:
- `POPk` (0x82) is the canonical NOP.
- Comparisons are unsigned; the result is always a single byte even in short mode.
- Address operands for LDZ/STZ/LDR/STR, the port for DEI/DEO, the JCN condition and the SFT shift byte stay 1 byte in short mode; only the value/address-on-stack widens where noted.
- JSR's pushed return address = address of the instruction after the JSR.

## 4. Uxntal language (assembler input)

All numbers are lowercase unsigned hexadecimal, 2 chars (byte) or 4 chars (short). Whitespace-separated tokens; the first char (rune) determines token type. Opcode mnemonics are uppercase with lowercase mode suffixes. A token like `add`, `bed`, `cafe` that parses as hex is a number, not a label — label names must be non-numeric, non-opcode, non-runic.

| Rune | Meaning | Assembles to |
|---|---|---|
| `( )` | comment (nestable; parens must be whitespace-separated tokens) | nothing |
| `[ ]` | ignored, readability only | nothing |
| `#xx` / `#xxxx` | literal number | `LIT xx` / `LIT2 xx xx` |
| `xx` / `xxxx` bare hex | raw byte/short data | the bytes |
| `"word` | raw ASCII bytes, no escapes, no spaces (write `20` explicitly) | the bytes |
| `\|addr` | absolute padding: set assembly position (also `\|00` for enum/struct defs; padding emits no bytes) | — |
| `$n` | relative padding: skip n bytes | — |
| `@name` | define parent label (opens scope) | — |
| `&name` | define child label (full name `scope/name`) | — |
| `,label` | literal relative address | `LIT s8-offset` (−128..+127 from use site) |
| `.label` | literal zero-page address | `LIT addr8` |
| `;label` | literal absolute address | `LIT2 addr16` |
| `_label` | raw relative byte | s8 offset |
| `-label` | raw zero-page byte | addr8 |
| `=label` | raw absolute short | addr16 |
| `!label` | jump | `JMI` + rel s16 |
| `?label` | conditional jump (pops flag byte; jumps when flag is **true**, so `?{ ... }` bodies run on false — "unless" blocks) | `JCI` + rel s16 |
| bare `label` | subroutine call | `JSI` + rel s16 |
| `{ }` | anonymous label: `{` references the address of its matching `}`; usable as `?{`, `!{`, `;{`, `_{`, `={`; nestable; scope-neutral | — |
| `%name { body }` | macro: textual inline expansion, must be defined before use; may contain anonymous labels; labels inside collide if expanded twice in one scope | body |

Scope rules: `@parent` opens a scope; `&child` defines `parent/child`. Inside the scope, `/child` and `,&child`-style references resolve locally; `parent/child` works globally; scopes can be extended later by defining `@parent/newname`.

Conventions: ROMs start with `|0100` then reset code (`@on-reset ( -> ) ... BRK`). Stack-effect comments `( a b -- c )`; `*` suffix = short; `.` separates return-stack effects; `( -> )` marks vectors. Device ports are declared as zero-page-style structs, e.g. `|10 @Console/vector $2 &read $5 &type $1 &write $1 &error $1`, then used as `.Console/write DEO`.

Assembler output: bytes from `0x0100` to the highest emitted address; padded-but-unwritten gaps are zeros; trailing zeros may be omitted from the file. Idiomatic inline variable: `[ LIT2 &x $2 ]` patched via `,&x STR2` (self-modifying code).

## 5. Varvara device set

16 device slots × 16 ports; port byte = `0xDP` (high nibble device, low nibble port). `vector*` ports hold the short address evaluated on that device's event.

| Base | Device | Base | Device |
|---|---|---|---|
| 00 | System | 80 | Controller |
| 10 | Console | 90 | Mouse |
| 20 | Screen | a0 | File 1 |
| 30–60 | Audio 0–3 | b0 | File 2 |
| 70 | — | c0 | Datetime |
| | | d0 | reserved |
| | | e0–f0 | — |

Unassigned slots and unmanaged ports behave as plain memory (read back what was written). Reserved/empty slots may host implementation-specific devices. Writing a port does NOT guarantee read-back of the same value (e.g. Screen/width on a non-resizable host returns the actual size).

### System (00)
```
00 vector* (unused)   08 red*
02 expansion*         0a green*
04 wst                0c blue*
05 rst                0e debug
06 metadata*          0f state
```
- `expansion*`: write address of an operation struct, enabling memory beyond 64KB. **Banks**: emulators should support 16 banks × 65536 bytes; bank 0 = main RAM. Ops (fields big-endian; `*` = short):
  - `00 fill`: `length* bank* addr* value`
  - `01 cpyl`: `length* srcbank* srcaddr* dstbank* dstaddr*` (copy forward, first byte first)
  - `02 cpyr`: same fields, copy backward (last byte first) — the pair enables overlapping moves.
- `wst`/`rst`: read/write the two stack pointers directly.
- `metadata*`: address of ROM metadata (a 00 byte, then LF-separated ASCII strings).
- `red/green/blue*`: three shorts define 4 colors, one nibble per channel per color; color i = nibble i of r,g,b. Default palette: #fff #000 #7db #f62. Used as the Screen palette.
- `debug`: nonzero write → host prints both stacks (`WST .. <ptr`) or breaks into a step-debugger.
- `state`: if nonzero at the end of a vector → stop listening to events and exit; exit code = `state & 0x7f`; `0x80` = exit 0 (success), `0x01` = exit 1.

### Console (10)
```
10 vector*  12 read  17 type  18 write  19 error  1c addr*  1e mode  1f exec
```
- `vector*` fires per received byte; the byte is in `read`.
- `type`: 0 no-queue, 1 stdin, 2 argument, 3 argument-spacer, 4 argument-end. CLI args stream through the console vector after reset; spacer byte = LF (0x0a). During reset, type=0 means no args, 1 means args follow.
- `write` / `error`: write one byte to stdout / stderr.

### Screen (20)
```
20 vector*  22 width*  24 height*  26 auto  28 x*  2a y*  2c addr*  2e pixel  2f sprite
```
- `vector*` evaluated 60 times per second.
- Two layers (background, foreground); fg treats color0 as transparent. 4 colors from the System palette.
- `pixel` write, bits `MLYX..CC`: bit7 = fill mode (fill from x,y toward bottom-right edge; Y/X flip bits select other quadrants), bit6 = layer, bits5–4 = flip Y/X (fill only), bits1–0 = color.
- `sprite` write, bits `MLYX CCCC`: bit7 = 2bpp (else 1bpp), bit6 = layer, bits5–4 = flip Y/X, low nibble = blend mode (blend values divisible by 5 make color0 transparent). Tile data at `addr*`: 8 bytes/tile for 1bpp (ICN), 16 bytes for 2bpp (CHR, two planes).
- `auto`, bits `LLLL .AYX`: bits 0–2 auto-increment x / y / addr after each sprite write (x,y by 8 px; addr by 8 or 16 bytes); high nibble = number of EXTRA tiles per write (0–15 → up to 16 tiles per DEO); extras advance rightward (auto-x) / downward (auto-y), reversed by the sprite's flip bits.
- Reads of `width*`/`height*` always return actual dimensions.

### Audio 0–3 (30/40/50/60)
```
x0 vector*  x2 position*  x4 output  x8 adsr*  xa length*  xc addr*  xe volume  xf pitch
```
- Samples are unsigned 8-bit mono at `addr*`, length `length*`. Writing `pitch` (re)triggers the note: bit7 = play-once (don't loop), bits0–6 = note (MIDI-style, 0x30 ≈ middle C). `adsr*`: four 4-bit fields in 1/15-second units (`#ffff` = 4 s envelope); `0000` = no envelope. Envelope: 0→100% (A), →50% (D), hold 50% (S), →0% (R), linearly interpolated. `volume`: high nibble left, low nibble right (`#ff` = max both). `output` (read): current envelope loudness. `vector*` fires when a note ends.

### Controller (80)
```
80 vector*  82 button  83 key  84 player
```
- `vector*` fires on button press/release and key press; `key` holds the ASCII char for the event and is reset to 0 after the vector runs.
- `button` bitmask (NES-style): 01 A(Ctrl), 02 B(Alt), 04 Select(Shift), 08 Start(Home), 10 Up, 20 Down, 40 Left, 80 Right.
- `player`: id of the player for multi-controller setups.

### Mouse (90)
```
90 vector*  92 x*  94 y*  96 state  9a scrollx*  9c scrolly*
```
- `vector*` fires on move and button change. `state`: low-nibble button bitmask (mouse1=01, mouse1+mouse3=05). Scroll ports hold signed shorts (±1) and are zeroed immediately after the vector fires.

### File 1/2 (a0/b0)
```
a0 vector* (reserved)  a2 success*  a4 stat*  a6 delete  a7 append
a8 name*  aa length*  ac read*  ae write*
```
- Protocol: set `name*` (address of zero-terminated path; setting it closes the previously open file), set `length*` (buffer size), then write a RAM address to `read*` or `write*` to transfer up to `length*` bytes; bytes actually transferred → `success*` (0 = failure). Repeated writes to `read*`/`write*` continue from the prior position → chunked streaming of files larger than the buffer.
- `append` = 01: writes append; 00: truncate/replace. Missing files are created on write.
- `delete`: writing any value deletes the file (`success*` = 1 on success).
- Directory reads yield lines `XXXX name\n`; details field: hex size (zero-padded) for files, `????` if >64KB, `----` for directories, `!!!!` for missing. `stat*` writes just that details field for `name*` into a buffer.
- Sandboxed to the working directory; lengths exceeding addressable memory are clamped.

### Datetime (c0)
```
c0 year*  c2 month  c3 day  c4 hour  c5 minute  c6 second  c7 dotw  c8 doty*  ca isdst
```
- All fields zero-based except `day`; `dotw` 0 = Sunday. `year` is the full year (not offset).

## 6. Implementation & tooling notes

- **Uxn core vs device set**: DEI/DEO semantics are entirely host-defined. Varvara is the canonical set, but subsets and alternatives exist: `uxncli` implements console/file/datetime only (headless); embedded ports define custom devices; reserved/unused slots are explicitly for implementation-specific extensions. A conforming Uxn core runs any ROM; what DEI/DEO do depends on the attached system.
- **Threading**: the Varvara model is single-threaded by construction — vectors are evaluated sequentially and are non-interruptible. The Uxn core spec itself imposes no threading model, but programs rely on the sequential-vector guarantee since all vectors share RAM and stacks. Parallel implementations must preserve the illusion of sequential vector execution.
- **Determinism**: the core (no devices) is fully deterministic given RAM + stacks. Nondeterminism enters only via DEI/DEO side effects (datetime, input, file, console) and host vector scheduling. Useful for differential testing of interpreter backends.
- **Self-modifying code (SMC)**: legal and idiomatic (patching inline `LIT` operands as local variables). JIT invalidation must observe all RAM-write channels: STZ/STR/STA in every mode, LIT-adjacent patching, System/expansion fill/cpyl/cpyr into bank 0, and File reads targeting RAM.
- **Halting**: a vector ends at BRK; the program exits via System/state. A loop without BRK never returns control to the host.
- **Conformance tests**: `opctest.tal` (core opcode behavior) and `uxntal.acid.tal` (assembler/language support), both at wiki.xxiivv.com/etc/. Validate interpreters with the former, assemblers with the latter.
- **Reference implementations**: `uxnmin.c` / `uxn.c` (canonical C core), uxn11 (X11), uxnemu (SDL2), uxn32 (Win32), uxn5 (JS/HTML5). Self-hosted toolchain: drifblim (assembler), uxndis (disassembler), uxnlin (peephole linter), uxnbal (validator), uxnfor (formatter), beetbug (debugger). Notable third-party: Raven (Rust, MPL-2.0).
- **Disassembler hints**: ROMs have no header, sections or symbols; code and data are indistinguishable. Linear sweep misdecodes immediate operands (LIT/JCI/JMI/JSI embed 1–2 operand bytes in the stream); reliable output needs recursive traversal from 0x0100 plus addresses written to vector ports, or tolerance for mixed code/data.
- **Interpreter/JIT-relevant regularities**: the 8-bit opcode splits cleanly into 5-bit op + 3 mode bits → dispatch can be 256 specialized handlers or 32 generic ones; stack accesses never fault (mask with `& 0xff`); memory accesses never fault (mask with `& 0xffff`, plus the wrap rules in §2); immediate jumps are PC-relative → basic blocks are relocatable; zero-page ops are the fast-variable path.

## 7. Dispatch semantics summary

```
op = ram[pc++]                        // pc: u16, wraps
if (op & 0x1f) == 0:                  // distinguish by full byte:
    0x00 BRK | 0x20 JCI | 0x40 JMI | 0x60 JSI
    0x80 LIT | 0xa0 LIT2 | 0xc0 LITr | 0xe0 LIT2r
else:
    s   = (op & 0x40) ? RST : WST     // r-mode source stack
    w   = (op & 0x20) ? 2 : 1         // 2-mode width — but: JCN cond, SFT shift,
                                      // DEI/DEO port, LDZ/STZ/LDR/STR addr stay 1 byte
    if op & 0x80: save s.ptr          // k-mode
    pop operands from s
    if op & 0x80: restore s.ptr
    execute; push results to s        // STH/JSR push to the opposite stack
```
