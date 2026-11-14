"""
Microchip MCP2517FD / MCP2518FD SPI Controller Driver for pyLCC.
Designed for Raspberry Pi Pico / Pico 2 (RP2040 / RP2350) and other SPI microcontrollers.
Configured for OpenLCB 125 kbps with deep 31-message hardware RX FIFO to eliminate buffer overflow.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

import struct
from ..can import CanFrame
from ..transport.base import BaseTransport

# Attempt MicroPython machine imports
try:
    from machine import Pin, SPI
except (ImportError, AttributeError):
    Pin = None
    SPI = None


# --- MCP2517FD / MCP2518FD SPI Instructions ---
INSTRUCTION_RESET = 0x00
INSTRUCTION_WRITE = 0x02
INSTRUCTION_READ  = 0x03

# --- Register Addresses ---
REG_CiCON      = 0x000  # CAN Control Register
REG_CiNBTCFG   = 0x004  # Nominal Bit Time Configuration
REG_CiDBTCFG   = 0x008  # Data Bit Time Configuration
REG_CiTDC      = 0x00C  # Transmitter Delay Compensation
REG_CiINT      = 0x01C  # Interrupt Flags
REG_CiRXIF     = 0x020  # Receive Interrupt Status
REG_CiTXIF     = 0x024  # Transmit Interrupt Status
REG_CiTXREQ    = 0x02C  # Transmit Request
REG_CiTREC     = 0x034  # Transmit/Receive Error Count
REG_CiTEFCON   = 0x040  # Transmit Event FIFO Control
REG_CiTXQCON   = 0x050  # Transmit Queue Control
REG_CiTXQSTA   = 0x054  # Transmit Queue Status
REG_CiTXQUA    = 0x058  # Transmit Queue User Address

# FIFO 1 (Configured as RX FIFO)
REG_CiFIFOCON1 = 0x05C  # FIFO 1 Control
REG_CiFIFOSTA1 = 0x060  # FIFO 1 Status
REG_CiFIFOUA1  = 0x064  # FIFO 1 User Address

# Filter Registers (Filter 0)
REG_CiFLTCON0  = 0x1D0  # Filter Control 0
REG_CiFLTOBJ0  = 0x1F0  # Filter Object 0
REG_CiMASK0    = 0x270  # Filter Mask 0

# Oscillator and I/O Control
REG_OSC        = 0xE00  # Oscillator Control
REG_IOCON      = 0xE04  # I/O Control

# Message RAM Start
RAM_START_ADDR = 0x400

# Operation Modes (bits 30-28 of CiCON)
MODE_NORMAL_CAN20 = 0x0  # Normal CAN 2.0 Mode (Classic CAN for OpenLCB)
MODE_SLEEP        = 0x1
MODE_INTERNAL_LOOPBACK = 0x2
MODE_LISTEN_ONLY  = 0x3
MODE_CONFIGURATION = 0x4
MODE_EXTERNAL_LOOPBACK = 0x5
MODE_CLASSIC_CAN  = 0x6
MODE_RESTRICTED   = 0x7


class Mcp2518fdTransport(BaseTransport):
    """
    High-performance SPI CAN Transport for Microchip MCP2517FD / MCP2518FD.
    Provides a deep 31-frame hardware FIFO queue to eliminate buffer overruns
    under OpenLCB 125 kbps bus bursts.
    """
    def __init__(
        self,
        spi=None,
        cs_pin=None,
        int_pin=None,
        osc_freq_mhz: int = 20,
        baudrate: int = 125000,
        spi_id: int = 0,
        sck_pin: int = 18,
        mosi_pin: int = 19,
        miso_pin: int = 16,
        cs_num: int = 17,
    ):
        super().__init__()
        self._spi = spi
        self._cs = cs_pin
        self._int = int_pin
        self.osc_freq_mhz = osc_freq_mhz
        self.baudrate = baudrate
        self.spi_id = spi_id
        self.sck_pin = sck_pin
        self.mosi_pin = mosi_pin
        self.miso_pin = miso_pin
        self.cs_num = cs_num

        self._rx_task = None
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected and not self.is_closed

    # --- SPI Low-Level Operations ---

    def _select(self):
        if self._cs is not None:
            if hasattr(self._cs, "value"):
                self._cs.value(0)
            elif callable(self._cs):
                self._cs(0)

    def _deselect(self):
        if self._cs is not None:
            if hasattr(self._cs, "value"):
                self._cs.value(1)
            elif callable(self._cs):
                self._cs(1)

    def _write_reg32(self, addr: int, val: int):
        """Write a 32-bit register (little-endian per MCP2518FD spec)."""
        cmd = struct.pack(">H", ((INSTRUCTION_WRITE & 0x0F) << 12) | (addr & 0x0FFF))
        data = struct.pack("<I", val)
        self._select()
        try:
            self._spi.write(cmd)
            self._spi.write(data)
        finally:
            self._deselect()

    def _read_reg32(self, addr: int) -> int:
        """Read a 32-bit register."""
        cmd = struct.pack(">H", ((INSTRUCTION_READ & 0x0F) << 12) | (addr & 0x0FFF))
        self._select()
        try:
            self._spi.write(cmd)
            if hasattr(self._spi, "read"):
                raw = self._spi.read(4)
            else:
                raw = b"\x00\x00\x00\x00"
            return struct.unpack("<I", raw)[0]
        finally:
            self._deselect()

    def _write_bytes(self, addr: int, data: bytes):
        """Write arbitrary bytes to RAM or registers."""
        cmd = struct.pack(">H", ((INSTRUCTION_WRITE & 0x0F) << 12) | (addr & 0x0FFF))
        self._select()
        try:
            self._spi.write(cmd)
            self._spi.write(data)
        finally:
            self._deselect()

    def _read_bytes(self, addr: int, length: int) -> bytes:
        """Read arbitrary bytes from RAM or registers."""
        cmd = struct.pack(">H", ((INSTRUCTION_READ & 0x0F) << 12) | (addr & 0x0FFF))
        self._select()
        try:
            self._spi.write(cmd)
            if hasattr(self._spi, "read"):
                return self._spi.read(length)
            return b"\x00" * length
        finally:
            self._deselect()

    # --- Hardware Initialization ---

    async def connect(self):
        """Initialize SPI bus, configure MCP2518FD registers, and start RX loop."""
        if self._is_connected:
            return

        if self._spi is None:
            if SPI is None or Pin is None:
                raise RuntimeError(
                    "MicroPython SPI/Pin modules not available. Mcp2518fdTransport "
                    "requires a physical RP2040/RP2350 or custom SPI object."
                )

            # Initialize CS pin (active low, start high)
            self._cs = Pin(self.cs_num, Pin.OUT, value=1)
            # Initialize high-speed SPI (10 MHz)
            self._spi = SPI(
                self.spi_id,
                baudrate=10_000_000,
                polarity=0,
                phase=0,
                sck=Pin(self.sck_pin),
                mosi=Pin(self.mosi_pin),
                miso=Pin(self.miso_pin),
            )

        # 1. Reset Controller
        self._select()
        try:
            # 16-bit reset instruction: 0x0000
            self._spi.write(b"\x00\x00")
        finally:
            self._deselect()

        try:
            await asyncio.sleep_ms(5)
        except AttributeError:
            await asyncio.sleep(0.005)

        # 2. Configure Oscillator (divide by 1, disable PLL)
        self._write_reg32(REG_OSC, 0x00000000)

        # 3. Enter Configuration Mode
        cicon = self._read_reg32(REG_CiCON)
        cicon &= ~(0x07 << 28)
        cicon |= (MODE_CONFIGURATION << 28)
        self._write_reg32(REG_CiCON, cicon)

        # 4. Configure Nominal Bit Time (125 kbps)
        # For 20 MHz crystal: SYSCLK = 20 MHz
        # Bit rate = 125 kbps -> Total Tq = 160 (BRP=0) or 80 (BRP=1)
        # Using BRP=1: Tq = 100 ns. Total Tq = 80. Sync=1, Prop+Seg1=63, Seg2=16, SJW=16.
        if self.osc_freq_mhz == 40:
            # 40 MHz clock: BRP=3 (div by 4 = 10 MHz Tq clock). Total Tq = 80.
            brp = 3
        else:
            # 20 MHz clock (standard on breakouts)
            brp = 1
        tseg1 = 62  # programmed as (TSEG1 - 1)
        tseg2 = 15  # programmed as (TSEG2 - 1)
        sjw = 15    # programmed as (SJW - 1)

        nbtcfg = (brp << 24) | (tseg1 << 16) | (tseg2 << 8) | sjw
        self._write_reg32(REG_CiNBTCFG, nbtcfg)

        # 5. Configure Transmit Queue (TXQ)
        # FSIZE=7 (8 messages), PLSIZE=0 (8-byte payload), TXEN=1
        txqcon = (0 << 29) | (7 << 24) | (1 << 7)  # PLSIZE=0, FSIZE=7, TXEN=1
        self._write_reg32(REG_CiTXQCON, txqcon)

        # 6. Configure Receive FIFO 1 (Deep 31-message hardware FIFO)
        # FSIZE=30 (31 messages), PLSIZE=0 (8-byte payload), TXEN=0 (RX mode), RXIE=1
        fifo1con = (0 << 29) | (30 << 24) | (1 << 0)  # PLSIZE=0, FSIZE=30, RXIE=1
        self._write_reg32(REG_CiFIFOCON1, fifo1con)

        # 7. Configure Filter 0 to accept ALL 29-bit Extended frames into FIFO 1
        # CiFLTCON0: FLTEN=1, FBP=1 (point to FIFO 1)
        self._write_reg32(REG_CiFLTCON0, 0x81)
        # CiFLTOBJ0: EXIDE=1 (only match extended identifiers)
        self._write_reg32(REG_CiFLTOBJ0, (1 << 30))
        # CiMASK0: MIDE=1 (match IDE bit), all ID mask bits=0 (match all 29-bit IDs)
        self._write_reg32(REG_CiMASK0, (1 << 30))

        # 8. Enter Normal CAN 2.0 Operating Mode
        cicon = self._read_reg32(REG_CiCON)
        cicon &= ~(0x07 << 28)
        cicon |= (MODE_CLASSIC_CAN << 28)  # Classic CAN 2.0 mode
        self._write_reg32(REG_CiCON, cicon)

        self._is_connected = True
        self._is_closed = False
        self._rx_task = asyncio.create_task(self._rx_loop())

    # --- Frame Reception Pump ---

    async def _rx_loop(self):
        """Poll FIFO 1 in background and dispatch incoming frames."""
        while self._is_connected and not self.is_closed:
            try:
                # Check FIFO 1 status: bit 0 (RXIF) is set when FIFO is not empty
                sta = self._read_reg32(REG_CiFIFOSTA1)
                if sta & 0x01:  # Not empty!
                    # Get user address for head of FIFO 1 in RAM
                    ua = self._read_reg32(REG_CiFIFOUA1)
                    # Read 16-byte message object: 4-byte ID + 4-byte flags + 8-byte data
                    raw_msg = self._read_bytes(RAM_START_ADDR + ua, 16)

                    # Increment FIFO pointer (UINC = bit 8 of CiFIFOCON1)
                    self._write_reg32(REG_CiFIFOCON1, self._read_reg32(REG_CiFIFOCON1) | (1 << 8))

                    # Unpack CAN message
                    frame = self._unpack_can_message(raw_msg)
                    if frame is not None:
                        self._dispatch_frame(frame)
            except Exception:
                pass

            try:
                await asyncio.sleep_ms(1)
            except AttributeError:
                await asyncio.sleep(0.001)

    def _unpack_can_message(self, raw: bytes) -> CanFrame:
        """Unpack 16-byte MCP2518FD message object into pyLCC CanFrame."""
        if len(raw) < 16:
            return None

        word0, word1 = struct.unpack("<II", raw[:8])
        data_bytes = raw[8:16]

        # Word 0: Bits 10-0 = SID (bits 28-18 of 29-bit ID), Bits 28-11 = EID (bits 17-0)
        sid = word0 & 0x7FF
        eid = (word0 >> 11) & 0x3FFFF
        can_id = (sid << 18) | eid

        # Word 1: Bit 4 = IDE (Extended), Bit 5 = RTR, Bits 3-0 = DLC
        dlc = word1 & 0x0F
        is_extended = bool(word1 & (1 << 4))
        is_rtr = bool(word1 & (1 << 5))

        payload = data_bytes[:min(dlc, 8)]

        return CanFrame(
            can_id=can_id,
            data=payload,
            is_extended=is_extended,
            is_rtr=is_rtr,
        )

    # --- Frame Transmission ---

    async def send_frame(self, frame: CanFrame) -> bool:
        """Transmit a frame using the Transmit Queue (TXQ)."""
        if not self._is_connected:
            raise RuntimeError("Cannot send frame: MCP2518FD transport is not connected")

        # Check if TXQ is not full (bit 0 of CiTXQSTA: TXIF = 1 when space available)
        max_retries = 3
        for attempt in range(max_retries):
            sta = self._read_reg32(REG_CiTXQSTA)
            if sta & 0x01:
                # Space available in TXQ!
                ua = self._read_reg32(REG_CiTXQUA)

                # Pack 29-bit CAN ID into Word 0
                sid = (frame.can_id >> 18) & 0x7FF
                eid = frame.can_id & 0x3FFFF
                word0 = (eid << 11) | sid

                # Word 1: DLC, IDE=1, RTR
                dlc = len(frame.data)
                ide = 1 if frame.is_extended else 0
                rtr = 1 if frame.is_rtr else 0
                word1 = (rtr << 5) | (ide << 4) | (dlc & 0x0F)

                padded_data = frame.data + b"\x00" * (8 - len(frame.data))
                msg_bytes = struct.pack("<II", word0, word1) + padded_data[:8]

                # Write to RAM
                self._write_bytes(RAM_START_ADDR + ua, msg_bytes)

                # Increment TXQ pointer and request transmission (UINC=bit 8, TXREQ=bit 9)
                txqcon = self._read_reg32(REG_CiTXQCON)
                txqcon |= (1 << 8) | (1 << 9)
                self._write_reg32(REG_CiTXQCON, txqcon)
                return True

            if attempt < max_retries - 1:
                try:
                    await asyncio.sleep_ms(1)
                except AttributeError:
                    await asyncio.sleep(0.001)

        return False

    async def close(self):
        """Stop background RX task and set controller to sleep mode."""
        self._is_connected = False
        await super().close()

        if self._rx_task is not None:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except (asyncio.CancelledError, Exception):
                pass
            self._rx_task = None

        try:
            # Place controller in sleep mode
            cicon = self._read_reg32(REG_CiCON)
            cicon &= ~(0x07 << 28)
            cicon |= (MODE_SLEEP << 28)
            self._write_reg32(REG_CiCON, cicon)
        except Exception:
            pass
