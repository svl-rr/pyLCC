"""
Unit tests for STM32 pyb.CAN Transport.
"""

import unittest
import asyncio
from pylcc.can import CanFrame
from pylcc.hardware.stm32_can import Stm32CanTransport
from pylcc.node import LCCNode
from pylcc.components import LCCTurnout


class MockPybCan:
    """Mock MicroPython pyb.CAN peripheral for automated testing."""
    NORMAL = 0
    MASK32 = 0

    def __init__(self, bus_id=1, mode=0, baudrate=125000, extframe=True, **kwargs):
        self.bus_id = bus_id
        self.mode = mode
        self.baudrate = baudrate
        self.extframe = extframe
        self.filter_configured = False
        self.tx_frames = []
        self.rx_frames = []
        self.deinitialized = False

    def setfilter(self, bank, mode, fifo, params):
        self.filter_configured = True

    def any(self, fifo: int = 0) -> int:
        return len(self.rx_frames)

    def recv(self, fifo: int = 0, timeout: int = 0):
        if self.rx_frames:
            return self.rx_frames.pop(0)
        return None

    def send(self, data, can_id, timeout=10, is_extended=True):
        self.tx_frames.append((can_id, bytes(data), is_extended))

    def deinit(self):
        self.deinitialized = True


class TestStm32CanTransport(unittest.IsolatedAsyncioTestCase):
    """Test Stm32CanTransport driver."""

    async def asyncSetUp(self):
        self.mock_can = MockPybCan()
        self.transport = Stm32CanTransport(bus_id=1, can_obj=self.mock_can)
        await self.transport.connect()

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_connect_and_filter_setup(self):
        self.assertTrue(self.transport.is_connected)
        self.assertTrue(self.mock_can.filter_configured)

    async def test_send_frame(self):
        frame = CanFrame(can_id=0x195B4401, data=b"\xCA\xFE\xBA\xBE", is_extended=True)
        ok = await self.transport.send_frame(frame)
        self.assertTrue(ok)

        self.assertEqual(len(self.mock_can.tx_frames), 1)
        can_id, data, is_ext = self.mock_can.tx_frames[0]
        self.assertEqual(can_id, 0x195B4401)
        self.assertEqual(data, b"\xCA\xFE\xBA\xBE")
        self.assertTrue(is_ext)

    async def test_receive_frame(self):
        received = []
        self.transport.add_listener(lambda f: received.append(f))

        # pyb.CAN tuple: (id, is_extended, is_rtr, fmi, data)
        self.mock_can.rx_frames.append((0x195B4402, True, False, 0, b"\x12\x34"))

        # Wait briefly for RX loop
        await asyncio.sleep(0.02)

        self.assertEqual(len(received), 1)
        f = received[0]
        self.assertEqual(f.can_id, 0x195B4402)
        self.assertEqual(f.data, b"\x12\x34")
        self.assertTrue(f.is_extended)

    async def test_close_deinitializes(self):
        await self.transport.close()
        self.assertFalse(self.transport.is_connected)
        self.assertTrue(self.mock_can.deinitialized)

    async def test_node_integration(self):
        node = LCCNode("05.01.01.01.14.03", self.transport)
        turnout = LCCTurnout("STM32 Turnout", close_event="05.01.01.01.14.03.00.01", throw_event="05.01.01.01.14.03.00.02")
        node.add_component(turnout)

        await node.start(check_delay_ms=10)
        self.assertTrue(node.is_permitted)
        await node.stop()


if __name__ == "__main__":
    unittest.main()
