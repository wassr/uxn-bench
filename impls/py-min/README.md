# py-min

A minimal, stdlib-only Uxntal assembler (`asm.py`) and Uxn CPU interpreter
(`vm.py`, varvara-headless: System + Console only). Built as the first entry
in `impls/` — a testbed for the coredump-based verification path (see
`../../coredump_design.md`), and as a reference point for trying out
different interpreter languages/dispatch styles later.

## Declared capabilities

Registered in `uxn-bench.json` as `py-min`:

- Assembler: `assembler`, `assembler/jsi`, `assembler/jcn`, `assembler/jmi`,
  `assembler/runes`, `assembler/lambda`
- Interpreter: `cpu`, `cpu/wrap`, `cpu/stack-wrap`, `cpu/coredump`,
  `varvara/system`, `varvara/console`

Verified byte-for-byte against the vendored `uxnasm` across every test under
`tests/cpu/` and `tests/assembler/` (excluding the ones below), and the full
`uxn-bench` suite passes 0-fail / appropriately-skipped when `py-min`
is configured as both assembler and interpreter.

## Known limitations (intentional, not bugs)

- **`assembler/pad-label` is not implemented.** `|label` / `$label` (using a
  label name instead of a raw hex address as the padding operand) errors
  with "Padding invalid". This affects
  `tests/assembler/features/{pad_abs,pad_lab,rewind}.test.json` and
  `tests/assembler/uxntal_acid.test.json`, all of which already require this
  capability and so skip cleanly.
- **No event-vector dispatch.** `vm.py` runs the reset vector (`0x0100`)
  once, straight-line, to completion (`BRK`). There is no Console/Screen/etc.
  vector evaluation loop, so per-byte Console stdin vectoring
  (`tests/cpu/edgecases/async_console.test.json`) isn't meaningfully
  exercised — that test happens to still report a (trivial, vacuous) pass
  against `python-minimal` because its assertions are weak enough not to
  notice, not because the behavior is actually implemented.
- **No traps.** Division by zero yields 0 and stack/PC/memory access always
  wraps (`cpu/wrap`, `cpu/stack-wrap`) — matching the canonical "no invalid
  programs" Uxn spec, not the `cpu/trap-on-stack` / `cpu/trap-on-div-zero`
  variant behavior some other interpreters opt into.
- **Device page is otherwise inert.** Only System (`wst`/`rst` pointers at
  ports `0x04`/`0x05`, halt-request at `0x0f`) and Console (`write`/`error`
  at `0x18`/`0x19`) have real behavior. Every other port (Screen, Audio,
  Controller, Mouse, File, Datetime, and the rest of System) just stores
  whatever byte was last written and reads it back — plain memory, no
  Varvara behavior, consistent with those capabilities not being declared.

## Coredump format

`vm.py --dump-on-halt PATH` writes the v2 binary format described in
`../../coredump_design.md`: `UXNC` magic, 1-byte version (2), WST/RST pointers,
halt reason, exit code, PC, presence flags bitmask, WST/RST arrays, Device I/O page,
64KB RAM, and cycle counter.

## Running benchmarks

From the repository root, run:

```sh
python3 -m buxn bench run --interpreter py-min --runs 1
```

Benchmarks include two warm-up executions in addition to the measurements.
The Python interpreter can take several minutes per execution for large
workloads such as bubble sort. The default benchmark timeout is 300 seconds
per execution; increase it with `--timeout 600` on slower machines. Both
`bench run` and `bench compare` accept this option.
