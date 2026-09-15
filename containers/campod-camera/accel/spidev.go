package main

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

// Linux struct spi_ioc_transfer. 32 bytes; the field order and widths are the
// kernel's, so do not reorder them.
type spiTransfer struct {
	txBuf          uint64
	rxBuf          uint64
	length         uint32
	speedHz        uint32
	delayUsecs     uint16
	bitsPerWord    uint8
	csChange       uint8
	txNBits        uint8
	rxNBits        uint8
	wordDelayUsecs uint8
	pad            uint8
}

const (
	spiIOCWrMode        = 0x40016b01 // _IOW('k', 1, __u8)
	spiIOCWrBitsPerWord = 0x40016b03 // _IOW('k', 3, __u8)
	spiIOCWrMaxSpeedHz  = 0x40046b04 // _IOW('k', 4, __u32)

	spiTransferSize = 32
)

// SPI_IOC_MESSAGE(n) encodes the transfer count in the ioctl's size field, which
// is 14 bits -- so at most 511 transfers. We use at most 33.
func spiIOCMessage(n int) uintptr {
	return uintptr((1 << 30) | ((n * spiTransferSize) << 16) | ('k' << 8))
}

// adxl345 SPI framing: bit 7 set = read, bit 6 set = multi-byte.
const (
	regRead  = 0x80
	regMulti = 0x40
)

// spi is the seam the tests replace. It is deliberately expressed in terms of
// what the part needs rather than in terms of ioctls, so a fake can implement
// the semantics without emulating kernel structs.
type spi interface {
	WriteReg(reg, val byte) error
	ReadRegs(reg byte, n int) ([]byte, error)
	// DrainFIFO reads count FIFO entries in ONE ioctl and returns the raw
	// bytes, 6 per entry. See the comment on the implementation for why this
	// being a single ioctl is the whole point of this package existing.
	DrainFIFO(count int, dst []byte) ([]byte, error)
	Close() error
}

type spidev struct {
	f       *os.File
	speedHz uint32
	// Preallocated so the hot path never allocates: one transfer descriptor per
	// FIFO entry, plus the scratch the kernel DMAs into.
	xfers []spiTransfer
	txBuf []byte
	rxBuf []byte
}

func openSPI(path string, mode uint8, speedHz uint32) (*spidev, error) {
	f, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil {
		return nil, err
	}
	d := &spidev{
		f:       f,
		speedHz: speedHz,
		xfers:   make([]spiTransfer, fifoDepth+1),
		txBuf:   make([]byte, (fifoDepth+1)*bytesPerRead),
		rxBuf:   make([]byte, (fifoDepth+1)*bytesPerRead),
	}
	bits := uint8(8)
	if err := d.ioctlPtr(spiIOCWrMode, unsafe.Pointer(&mode)); err != nil {
		f.Close()
		return nil, fmt.Errorf("set SPI mode: %w", err)
	}
	if err := d.ioctlPtr(spiIOCWrBitsPerWord, unsafe.Pointer(&bits)); err != nil {
		f.Close()
		return nil, fmt.Errorf("set bits per word: %w", err)
	}
	if err := d.ioctlPtr(spiIOCWrMaxSpeedHz, unsafe.Pointer(&speedHz)); err != nil {
		f.Close()
		return nil, fmt.Errorf("set max speed: %w", err)
	}
	return d, nil
}

func (d *spidev) ioctlPtr(req uintptr, arg unsafe.Pointer) error {
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, d.f.Fd(), req, uintptr(arg))
	if errno != 0 {
		return errno
	}
	return nil
}

func (d *spidev) one(tx, rx []byte) error {
	tr := spiTransfer{
		txBuf:       uint64(uintptr(unsafe.Pointer(&tx[0]))),
		rxBuf:       uint64(uintptr(unsafe.Pointer(&rx[0]))),
		length:      uint32(len(tx)),
		speedHz:     d.speedHz,
		bitsPerWord: 8,
	}
	return d.ioctlPtr(spiIOCMessage(1), unsafe.Pointer(&tr))
}

func (d *spidev) WriteReg(reg, val byte) error {
	tx := []byte{reg, val}
	rx := make([]byte, 2)
	return d.one(tx, rx)
}

func (d *spidev) ReadRegs(reg byte, n int) ([]byte, error) {
	hdr := reg | regRead
	if n > 1 {
		hdr |= regMulti
	}
	tx := make([]byte, n+1)
	rx := make([]byte, n+1)
	tx[0] = hdr
	if err := d.one(tx, rx); err != nil {
		return nil, err
	}
	return rx[1:], nil
}

// DrainFIFO reads count FIFO entries in a SINGLE ioctl.
//
// This is the reason this reader is not Python. The Python implementation issued
// one ioctl per entry, and measured on a quiet campod-se that cost 105.5 us per
// sample against 37.3 us of actual clock time at 1.5 MHz -- 65% of the cost was
// per-transaction overhead, not bits on the wire. Two devices at 3200 Hz spent
// 3.37 ms per cycle draining, and the worst drains reached 19.93 ms, past the
// 10 ms it takes a 32-deep FIFO to overflow at 3200 Hz. That is where the
// steady-state 2.08 overruns/second came from.
//
// One ioctl carrying N transfers collapses N syscalls into one while keeping the
// part's requirements: cs_change deasserts CS between entries and delay_usecs
// spaces them, which satisfies the datasheet's 5 us FIFO-pop delay EXPLICITLY
// rather than relying on the incidental duration of the address byte. AN-1025
// states that delay as required between the end of reading the data registers
// and the start of the next FIFO or FIFO_STATUS read; the Python reader's
// comment quoted only half of that and relied on 1.5 MHz addressing to cover it.
func (d *spidev) DrainFIFO(count int, dst []byte) ([]byte, error) {
	if count <= 0 {
		return dst[:0], nil
	}
	if count > fifoDepth {
		count = fifoDepth
	}
	for i := 0; i < count; i++ {
		off := i * bytesPerRead
		d.txBuf[off] = regDataX0 | regRead | regMulti
		for j := 1; j < bytesPerRead; j++ {
			d.txBuf[off+j] = 0
		}
		d.xfers[i] = spiTransfer{
			txBuf:       uint64(uintptr(unsafe.Pointer(&d.txBuf[off]))),
			rxBuf:       uint64(uintptr(unsafe.Pointer(&d.rxBuf[off]))),
			length:      bytesPerRead,
			speedHz:     d.speedHz,
			bitsPerWord: 8,
			// Deassert CS and wait between entries. Both are what the datasheet
			// asks for above 1.6 MHz and neither costs anything meaningful here:
			// 5 us x 16 entries is 80 us against a 10 ms overflow bound.
			csChange:   1,
			delayUsecs: popDelayUsec,
		}
	}
	if err := d.ioctlPtr(spiIOCMessage(count), unsafe.Pointer(&d.xfers[0])); err != nil {
		return nil, err
	}
	out := dst[:0]
	for i := 0; i < count; i++ {
		off := i * bytesPerRead
		out = append(out, d.rxBuf[off+1:off+bytesPerRead]...)
	}
	return out, nil
}

func (d *spidev) Close() error { return d.f.Close() }
