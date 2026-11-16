package main

import (
	"encoding/binary"
	"fmt"
	"io"
	"os"
)

const (
	RAM_SIZE   = 0x10000
	STACK_SIZE = 256

	FLAG_WST    uint16 = 0x0001
	FLAG_RST    uint16 = 0x0002
	FLAG_DEV    uint16 = 0x0004
	FLAG_RAM    uint16 = 0x0008
	FLAG_CYCLES uint16 = 0x0010
	FLAG_BANKS  uint16 = 0x0020
)

type Stack struct {
	Data [STACK_SIZE]byte
	Ptr  uint8
}

func (s *Stack) push8(v byte) {
	s.Data[s.Ptr] = v
	s.Ptr++
}

func (s *Stack) pop8() byte {
	s.Ptr--
	return s.Data[s.Ptr]
}

func (s *Stack) peek8(depth uint8) byte {
	return s.Data[uint8(s.Ptr-1-depth)]
}

func (s *Stack) push16(v uint16) {
	s.push8(byte(v >> 8))
	s.push8(byte(v))
}

func (s *Stack) pop16() uint16 {
	lo := s.pop8()
	hi := s.pop8()
	return (uint16(hi) << 8) | uint16(lo)
}

func (s *Stack) peek16(depth uint8) uint16 {
	lo := s.peek8(depth * 2)
	hi := s.peek8(depth*2 + 1)
	return (uint16(hi) << 8) | uint16(lo)
}

func (s *Stack) push(v uint16, short bool) {
	if short {
		s.push16(v)
	} else {
		s.push8(byte(v))
	}
}

func (s *Stack) pop(short bool) uint16 {
	if short {
		return s.pop16()
	}
	return uint16(s.pop8())
}

func (s *Stack) peek(depth uint8, short bool) uint16 {
	if short {
		return s.peek16(depth)
	}
	return uint16(s.peek8(depth))
}

type Devices struct {
	Wst      *Stack
	Rst      *Stack
	Page     [256]byte
	Halted   bool
	ExitCode int
}

func (d *Devices) Dei(port byte) byte {
	if port == 0x02 || port == 0x04 {
		return d.Wst.Ptr
	}
	if port == 0x03 || port == 0x05 {
		return d.Rst.Ptr
	}
	return d.Page[port]
}

func (d *Devices) Deo(port byte, val byte) {
	d.Page[port] = val
	if port == 0x02 || port == 0x04 {
		d.Wst.Ptr = val
	} else if port == 0x03 || port == 0x05 {
		d.Rst.Ptr = val
	} else if port == 0x0f && val != 0 {
		d.Halted = true
		if val == 0x80 {
			d.ExitCode = 0
		} else {
			d.ExitCode = int(val & 0x7f)
		}
	} else if port == 0x18 {
		os.Stdout.Write([]byte{val})
	} else if port == 0x19 {
		os.Stderr.Write([]byte{val})
	}
}

type Uxn struct {
	Ram        [RAM_SIZE]byte
	Wst        Stack
	Rst        Stack
	Devices    Devices
	Pc         uint16
	Cycles     uint64
	HaltReason byte
}

func NewUxn() *Uxn {
	u := &Uxn{}
	u.Devices.Wst = &u.Wst
	u.Devices.Rst = &u.Rst
	u.Pc = 0x0100
	return u
}

func (u *Uxn) loadROM(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	n, err := f.Read(u.Ram[0x0100:])
	if err != nil && err != io.EOF {
		return err
	}
	_ = n
	return nil
}

func (u *Uxn) readMem(addr uint16, short bool, zp bool) uint16 {
	mask := uint16(0xffff)
	if zp {
		mask = 0xff
	}
	if short {
		hi := u.Ram[addr&mask]
		lo := u.Ram[(addr+1)&mask]
		return (uint16(hi) << 8) | uint16(lo)
	}
	return uint16(u.Ram[addr&mask])
}

func (u *Uxn) writeMem(addr uint16, val uint16, short bool, zp bool) {
	mask := uint16(0xffff)
	if zp {
		mask = 0xff
	}
	if short {
		u.Ram[addr&mask] = byte(val >> 8)
		u.Ram[(addr+1)&mask] = byte(val)
	} else {
		u.Ram[addr&mask] = byte(val)
	}
}

func (u *Uxn) dei16(port byte, short bool) uint16 {
	if short {
		hi := u.Devices.Dei(port)
		lo := u.Devices.Dei(port + 1)
		return (uint16(hi) << 8) | uint16(lo)
	}
	return uint16(u.Devices.Dei(port))
}

func (u *Uxn) deo16(port byte, val uint16, short bool) {
	if short {
		u.Devices.Deo(port, byte(val>>8))
		u.Devices.Deo(port+1, byte(val))
	} else {
		u.Devices.Deo(port, byte(val))
	}
}

func (u *Uxn) Run() {
	u.Pc = 0x0100
	for {
		if u.Devices.Halted {
			u.HaltReason = 1
			break
		}
		instr := u.Ram[u.Pc]
		prevPc := u.Pc
		u.Pc++
		u.Cycles++
		if instr == 0x00 { // BRK
			u.Pc = prevPc
			u.HaltReason = 0
			break
		}
		u.Pc = u.exec(instr, u.Pc)
		if u.Devices.Halted {
			u.HaltReason = 1
			break
		}
	}
}


func (u *Uxn) exec(instr byte, pc uint16) uint16 {
	base := instr & 0x1f
	if base == 0x00 {
		return u.execImmediate(instr, pc)
	}

	keep := (instr & 0x80) != 0
	ret := (instr & 0x40) != 0
	short := (instr & 0x20) != 0

	var src, dst *Stack
	if ret {
		src = &u.Rst
		dst = &u.Wst
	} else {
		src = &u.Wst
		dst = &u.Rst
	}

	var mask uint16 = 0xff
	if short {
		mask = 0xffff
	}

	switch base {
	case 0x01: // INC
		a := src.peek(0, short)
		if !keep {
			src.pop(short)
		}
		src.push((a+1)&mask, short)
	case 0x02: // POP
		if !keep {
			src.pop(short)
		}
	case 0x03: // NIP
		var b uint16
		if keep {
			b = src.peek(0, short)
			src.peek(1, short)
		} else {
			b = src.pop(short)
			src.pop(short)
		}
		src.push(b, short)
	case 0x04: // SWP
		var a, b uint16
		if keep {
			b = src.peek(0, short)
			a = src.peek(1, short)
		} else {
			b = src.pop(short)
			a = src.pop(short)
		}
		src.push(b, short)
		src.push(a, short)
	case 0x05: // ROT
		var a, b, c uint16
		if keep {
			c = src.peek(0, short)
			b = src.peek(1, short)
			a = src.peek(2, short)
		} else {
			c = src.pop(short)
			b = src.pop(short)
			a = src.pop(short)
		}
		src.push(b, short)
		src.push(c, short)
		src.push(a, short)
	case 0x06: // DUP
		a := src.peek(0, short)
		if !keep {
			src.pop(short)
		}
		src.push(a, short)
		src.push(a, short)
	case 0x07: // OVR
		var a, b uint16
		if keep {
			b = src.peek(0, short)
			a = src.peek(1, short)
		} else {
			b = src.pop(short)
			a = src.pop(short)
		}
		src.push(a, short)
		src.push(b, short)
		src.push(a, short)

	case 0x08, 0x09, 0x0a, 0x0b: // EQU, NEQ, GTH, LTH
		var a, b uint16
		if keep {
			b = src.peek(0, short)
			a = src.peek(1, short)
		} else {
			b = src.pop(short)
			a = src.pop(short)
		}
		var res byte
		switch base {
		case 0x08:
			if a == b {
				res = 1
			}
		case 0x09:
			if a != b {
				res = 1
			}
		case 0x0a:
			if a > b {
				res = 1
			}
		case 0x0b:
			if a < b {
				res = 1
			}
		}
		src.push8(res)
	case 0x0c: // JMP
		var addr uint16
		if keep {
			addr = src.peek(0, short)
		} else {
			addr = src.pop(short)
		}
		if short {
			pc = addr
		} else {
			pc = uint16((int32(pc) + int32(int8(addr))) & 0xffff)
		}
	case 0x0d: // JCN
		var addr, cond uint16
		if keep {
			if short {
				addr = src.peek16(0)
				cond = uint16(src.peek8(2))
			} else {
				addr = uint16(src.peek8(0))
				cond = uint16(src.peek8(1))
			}
		} else {
			if short {
				addr = src.pop16()
			} else {
				addr = uint16(src.pop8())
			}
			cond = uint16(src.pop8())
		}
		if cond != 0 {
			if short {
				pc = addr
			} else {
				pc = uint16((int32(pc) + int32(int8(addr))) & 0xffff)
			}
		}
	case 0x0e: // JSR
		var addr uint16
		if keep {
			addr = src.peek(0, short)
		} else {
			addr = src.pop(short)
		}
		dst.push16(pc)
		if short {
			pc = addr
		} else {
			pc = uint16((int32(pc) + int32(int8(addr))) & 0xffff)
		}
	case 0x0f: // STH
		a := src.peek(0, short)
		if !keep {
			src.pop(short)
		}
		dst.push(a, short)
	case 0x10: // LDZ
		var addr byte
		if keep {
			addr = src.peek8(0)
		} else {
			addr = src.pop8()
		}
		src.push(u.readMem(uint16(addr), short, true), short)
	case 0x11: // STZ
		addr, val := u.popStoreOperands(src, keep, short, false)
		u.writeMem(addr, val, short, true)
	case 0x12: // LDR
		var addr byte
		if keep {
			addr = src.peek8(0)
		} else {
			addr = src.pop8()
		}
		target := uint16((int32(pc) + int32(int8(addr))) & 0xffff)
		src.push(u.readMem(target, short, false), short)
	case 0x13: // STR
		addr, val := u.popStoreOperands(src, keep, short, false)
		target := uint16((int32(pc) + int32(int8(addr))) & 0xffff)
		u.writeMem(target, val, short, false)
	case 0x14: // LDA
		var addr uint16
		if keep {
			addr = src.peek16(0)
		} else {
			addr = src.pop16()
		}
		src.push(u.readMem(addr, short, false), short)
	case 0x15: // STA
		addr, val := u.popStoreOperands(src, keep, short, true)
		u.writeMem(addr, val, short, false)
	case 0x16: // DEI
		var port byte
		if keep {
			port = src.peek8(0)
		} else {
			port = src.pop8()
		}
		src.push(u.dei16(port, short), short)
	case 0x17: // DEO
		port, val := u.popStoreOperands(src, keep, short, false)
		u.deo16(byte(port), val, short)
	case 0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d, 0x1e: // ADD, SUB, MUL, DIV, AND, ORA, EOR
		var a, b uint16
		if keep {
			b = src.peek(0, short)
			a = src.peek(1, short)
		} else {
			b = src.pop(short)
			a = src.pop(short)
		}
		var res uint16
		switch base {
		case 0x18:
			res = a + b
		case 0x19:
			res = a - b
		case 0x1a:
			res = a * b
		case 0x1b:
			if b != 0 {
				res = a / b
			} else {
				res = 0
			}
		case 0x1c:
			res = a & b
		case 0x1d:
			res = a | b
		case 0x1e:
			res = a ^ b
		}
		src.push(res&mask, short)
	case 0x1f: // SFT
		var shift byte
		var a uint16
		if keep {
			shift = src.peek8(0)
			if short {
				a = (uint16(src.peek8(2)) << 8) | uint16(src.peek8(1))
			} else {
				a = uint16(src.peek8(1))
			}
		} else {
			shift = src.pop8()
			if short {
				a = src.pop16()
			} else {
				a = uint16(src.pop8())
			}
		}
		res := (a >> (shift & 0x0f)) << ((shift >> 4) & 0x0f)
		src.push(res&mask, short)
	}

	return pc
}

func (u *Uxn) popStoreOperands(src *Stack, keep bool, short bool, addrShort bool) (uint16, uint16) {
	var addrWidth uint8 = 1
	if addrShort {
		addrWidth = 2
	}

	var addr, val uint16
	if keep {
		if addrShort {
			addr = src.peek16(0)
		} else {
			addr = uint16(src.peek8(0))
		}
		if short {
			val = (uint16(src.peek8(addrWidth+1)) << 8) | uint16(src.peek8(addrWidth))
		} else {
			val = uint16(src.peek8(addrWidth))
		}
	} else {
		if addrShort {
			addr = src.pop16()
		} else {
			addr = uint16(src.pop8())
		}
		if short {
			val = src.pop16()
		} else {
			val = uint16(src.pop8())
		}
	}
	return addr, val
}

func (u *Uxn) execImmediate(instr byte, pc uint16) uint16 {
	switch instr {
	case 0x20: // JCI
		cond := u.Wst.pop8()
		offset := u.readOffset16(pc)
		if cond != 0 {
			return uint16((int32(pc) + 2 + int32(offset)) & 0xffff)
		}
		return (pc + 2) & 0xffff
	case 0x40: // JMI
		offset := u.readOffset16(pc)
		return uint16((int32(pc) + 2 + int32(offset)) & 0xffff)
	case 0x60: // JSI
		offset := u.readOffset16(pc)
		u.Rst.push16((pc + 2) & 0xffff)
		return uint16((int32(pc) + 2 + int32(offset)) & 0xffff)
	case 0x80: // LIT
		u.Wst.push8(u.Ram[pc])
		return (pc + 1) & 0xffff
	case 0xa0: // LIT2
		u.Wst.push16((uint16(u.Ram[pc]) << 8) | uint16(u.Ram[(pc+1)&0xffff]))
		return (pc + 2) & 0xffff
	case 0xc0: // LITr
		u.Rst.push8(u.Ram[pc])
		return (pc + 1) & 0xffff
	case 0xe0: // LIT2r
		u.Rst.push16((uint16(u.Ram[pc]) << 8) | uint16(u.Ram[(pc+1)&0xffff]))
		return (pc + 2) & 0xffff
	}
	return pc
}

func (u *Uxn) readOffset16(pc uint16) int16 {
	raw := u.readMem(pc, true, false)
	return int16(raw)
}

func writeCoredump(path string, u *Uxn) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()

	flags := FLAG_WST | FLAG_RST | FLAG_DEV | FLAG_RAM | FLAG_CYCLES
	header := make([]byte, 16)
	copy(header[0:4], "UXNC")
	header[4] = 2
	header[5] = u.Wst.Ptr
	header[6] = u.Rst.Ptr
	header[7] = u.HaltReason
	header[8] = byte(u.Devices.ExitCode)
	binary.BigEndian.PutUint16(header[9:11], u.Pc)
	header[11] = 0 // Bank Count
	binary.BigEndian.PutUint16(header[12:14], flags)
	header[14] = 0
	header[15] = 0

	if _, err := f.Write(header); err != nil {
		return err
	}
	if _, err := f.Write(u.Wst.Data[:]); err != nil {
		return err
	}
	if _, err := f.Write(u.Rst.Data[:]); err != nil {
		return err
	}
	if _, err := f.Write(u.Devices.Page[:]); err != nil {
		return err
	}
	if _, err := f.Write(u.Ram[:]); err != nil {
		return err
	}

	cyclesBuf := make([]byte, 8)
	binary.BigEndian.PutUint64(cyclesBuf, u.Cycles)
	if _, err := f.Write(cyclesBuf); err != nil {
		return err
	}

	return nil
}


func main() {
	var romPath string
	var dumpPath string

	for i := 1; i < len(os.Args); i++ {
		arg := os.Args[i]
		if arg == "--dump-on-halt" || arg == "-dump-on-halt" {
			if i+1 < len(os.Args) {
				dumpPath = os.Args[i+1]
				i++
			} else {
				fmt.Fprintf(os.Stderr, "Error: --dump-on-halt requires an argument\n")
				os.Exit(1)
			}
		} else {
			romPath = arg
		}
	}

	if romPath == "" {
		fmt.Fprintf(os.Stderr, "usage: go-min [--dump-on-halt path] file.rom\n")
		os.Exit(1)
	}

	u := NewUxn()
	if err := u.loadROM(romPath); err != nil {
		fmt.Fprintf(os.Stderr, "failed to load ROM: %v\n", err)
		os.Exit(1)
	}

	u.Run()

	if dumpPath != "" {
		if err := writeCoredump(dumpPath, u); err != nil {
			fmt.Fprintf(os.Stderr, "failed to write coredump: %v\n", err)
			os.Exit(1)
		}
	}

	os.Exit(u.Devices.ExitCode)
}
