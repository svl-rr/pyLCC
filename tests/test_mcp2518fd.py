"""
Unit tests for Raspberry Pi Pico MCP2517FD / MCP2518FD SPI CAN Transport.
"""

import unittest
import asyncio
import struct
from pylcc.can import CanFrame
from pylcc.hardware.mcp2518fd import (
    Mcp2518fdTransport,
    REG_CiCON,
    REG_CiNBTCFG,
    REG_CiFIFOCON1,
    REG_CiFIFOSTA1,
    REG_CiFIFOUA1,
    REG_CiTXQCON,
    REG_CiTXQSTA,
    REG_CiTXQUA,
    RAM_START_ADDR,
    INSTRUCTION_READ,
    INSTRUCTION_WRITE,
    MODE_CLASSIC_CAN,
)
from pylcc.node import LCCNode
from pylcc.components import LCCTurnout


class MockSpiController:
    """Emulates MCP2518FD SPI bus and registers for automated testing."""
    def __init__(self):
        self.registers = {}
        self.ram = bytearray(2048)
        self.reset_called = False
        self._current_cmd = None
        self._current_addr = 0
        self._read_buffer = b""

        # Defaults
        self.registers[REG_CiCON] = 0x00000000
        self.registers[REG_CiTXQSTA] = 0x01  # Space available
        self.registers[REG_CiTXQUA] = 0x00   # User address 0
        self.registers[REG_CiFIFOSTA1] = 0x00 # Initially empty
        self.registers[REG_CiFIFOUA1] = 0x80  # User address 128

    def write(self, data: bytes):
        if data == b"\x00\x00":
            self.reset_called = True
            return

        if len(data) == 2:
            # Command header: 4-bit inst + 12-bit address
            cmd_val = struct.unpack(">H", data)[0]
            inst = (cmd_val >> 12) & 0x0F
            addr = cmd_val & 0x0FFF
            self._current_cmd = inst
            self._current_addr = addr
            return

        # Data payload following command header
        if self._current_cmd == INSTRUCTION_WRITE:
            if self._current_addr >= RAM_START_ADDR:
                offset = self._current_addr - RAM_START_ADDR
                self.ram[offset:offset + len(data)] = data
            elif len(data) == 4:
                val = struct.unpack("<I", data)[0]
                self.registers[self._current_addr] = val
                # If UINC is set in CiFIFOCON1, clear FIFO empty flag
                if self._current_addr == REG_CiFIFOCON1 and (val & (1 << 8)):
                    self.registers[REG_CiFIFOSTA1] = 0x00

    def read(self, length: int) -> bytes:
        if self._current_cmd == INSTRUCTION_READ:
            if self._current_addr >= RAM_START_ADDR:
                offset = self._current_addr - RAM_START_ADDR
                return bytes(self.ram[offset:offset + length])
            elif self._current_addr in self.registers:
                return struct.pack("<I", self.registers[self._current_addr])
            return b"\x00" * length
        return b"\x00" * length


class MockCsPin:
    def __init__(self):
        self.state = 1

    def value(self, val=None):
        if val is not None:
            self.state = val
        return self.state


class TestMcp2518fdTransport(unittest.IsolatedAsyncioTestCase):
    """Test Mcp2518fdTransport driver."""

    async def asyncSetUp(self):
        self.mock_spi = MockSpiController()
        self.mock_cs = MockCsPin()
        self.transport = Mcp2518fdTransport(
            spi=self.mock_spi,
            cs_pin=self.mock_cs,
            osc_freq_mhz=20,
            baudrate=125000,
        )
        await self.transport.connect()

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_initialization_sequence(self):
        """Verify controller was reset and configured for OpenLCB 125 kbps."""
        self.assertTrue(self.mock_spi.reset_called)
        self.assertTrue(self.transport.is_connected)

        # Check bit timing configuration register (125 kbps on 20 MHz)
        nbtcfg = self.mock_spi.registers.get(REG_CiNBTCFG)
        self.assertIsNotNone(nbtcfg)
        brp = (nbtcfg >> 24) & 0xFF
        self.assertEqual(brp, 1)

        # Check FIFO 1 configured for 31 messages (FSIZE=30)
        fifo1con = self.mock_spi.registers.get(REG_CiFIFOCON1)
        self.assertIsNotNone(fifo1con)
        fsize = (fifo1con >> 24) & 0x1F
        self.assertEqual(fsize, 30)

        # Check mode switched to Classic CAN
        cicon = self.mock_spi.registers.get(REG_CiCON)
        mode = (cicon >> 28) & 0x07
        self.assertEqual(mode, MODE_CLASSIC_CAN)

    async def test_send_frame(self):
        """Verify transmitting a 29-bit CAN frame packs correctly into RAM."""
        frame = CanFrame(can_id=0x195B422A, data=b"\x01\x02\x03\x04\x05\x06\x07\x08", is_extended=True)
        ok = await self.transport.send_frame(frame)
        self.assertTrue(ok)

        # Inspect RAM at TXQ user address (0)
        word0, word1 = struct.unpack("<II", self.mock_spi.ram[0:8])
        data_bytes = self.mock_spi.ram[8:16]

        # Check unpacked ID matches
        sid = word0 & 0x7FF
        eid = (word0 >> 11) & 0x3FFFF
        reconstructed_id = (sid << 18) | eid
        self.assertEqual(reconstructed_id, 0x195B422A)

        # Check IDE bit set and DLC=8
        self.assertTrue(bool(word1 & (1 << 4)))  # IDE
        self.assertEqual(word1 & 0x0F, 8)       # DLC
        self.assertEqual(data_bytes, b"\x01\x02\x03\x04\x05\x06\x07\x08")

    async def test_receive_frame(self):
        """Verify receiving a frame from FIFO 1 RAM dispatches to listener."""
        received = []
        self.transport.add_listener(lambda f: received.append(f))

        # Pack test message into RAM at FIFO 1 address (offset 128)
        can_id = 0x195B4301
        sid = (can_id >> 18) & 0x7FF
        eid = can_id & 0x3FFFF
        word0 = (eid << 11) | sid
        word1 = (1 << 4) | 4  # IDE=1, DLC=4
        payload = b"\xDE\xAD\xBE\xEF\x00\x00\x00\x00"

        offset = 128
        self.mock_spi.ram[offset:offset + 8] = struct.pack("<II", word0, word1)
        self.mock_spi.ram[offset + 8:offset + 16] = payload

        # Set FIFO 1 not empty
        self.mock_spi.registers[REG_CiFIFOSTA1] = 0x01

        # Wait briefly for RX loop
        await asyncio.sleep(0.02)

        self.assertEqual(len(received), 1)
        f = received[0]
        self.assertEqual(f.can_id, 0x195B4301)
        self.assertEqual(f.data, b"\xDE\xAD\xBE\xEF")
        self.assertTrue(f.is_extended)

    async def test_node_integration(self):
        """Verify LCCNode runs on Mcp2518fdTransport."""
        node = LCCNode("05.01.01.01.14.02", self.transport)
        turnout = LCCTurnout("Pico Turnout", close_event="05.01.01.01.14.02.00.01", throw_event="05.01.01.01.14.02.00.02")
        node.add_component(turnout)

        await node.start(check_delay_ms=10)
        self.assertTrue(node.is_permitted)
        await node.stop()


if __name__ == "__main__":
    unittest.main()
