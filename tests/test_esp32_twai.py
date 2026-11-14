"""
Unit tests for ESP32 / ESP32-C6 TWAI Transport.
"""

import unittest
import asyncio
from pylcc.can import CanFrame
from pylcc.hardware.esp32_twai import Esp32TwaiTransport
from pylcc.node import LCCNode
from pylcc.components import LCCTurnout


class MockTwaiPeripheral:
    """Mock MicroPython machine.TWAI peripheral for desktop automated testing."""
    NORMAL = 0

    def __init__(self):
        self.tx_frames = []
        self.rx_frames = []
        self.deinitialized = False
        self.fail_send_once = False

    def any(self) -> int:
        return len(self.rx_frames)

    def recv(self):
        if self.rx_frames:
            return self.rx_frames.pop(0)
        return None

    def send(self, can_id, data, is_ext=True, is_rtr=False):
        if self.fail_send_once:
            self.fail_send_once = False
            raise OSError("TX FIFO full")
        self.tx_frames.append((can_id, bytes(data), is_ext, is_rtr))

    def deinit(self):
        self.deinitialized = True


class TestEsp32TwaiTransport(unittest.IsolatedAsyncioTestCase):
    """Test Esp32TwaiTransport driver."""

    async def asyncSetUp(self):
        self.mock_twai = MockTwaiPeripheral()
        self.transport = Esp32TwaiTransport(tx_pin=5, rx_pin=4, twai_obj=self.mock_twai)
        await self.transport.connect()

    async def asyncTearDown(self):
        await self.transport.close()

    async def test_connect_and_state(self):
        self.assertTrue(self.transport.is_connected)
        self.assertFalse(self.transport.is_closed)

    async def test_send_frame(self):
        frame = CanFrame(can_id=0x195B4301, data=b"\x01\x02\x03\x04", is_extended=True)
        ok = await self.transport.send_frame(frame)
        self.assertTrue(ok)

        self.assertEqual(len(self.mock_twai.tx_frames), 1)
        can_id, data, is_ext, is_rtr = self.mock_twai.tx_frames[0]
        self.assertEqual(can_id, 0x195B4301)
        self.assertEqual(data, b"\x01\x02\x03\x04")
        self.assertTrue(is_ext)
        self.assertFalse(is_rtr)

    async def test_send_retry_on_fifo_full(self):
        self.mock_twai.fail_send_once = True
        frame = CanFrame(can_id=0x195B4302, data=b"\xAA\xBB", is_extended=True)
        ok = await self.transport.send_frame(frame)
        self.assertTrue(ok)
        self.assertEqual(len(self.mock_twai.tx_frames), 1)

    async def test_receive_frame_via_listener(self):
        received = []
        self.transport.add_listener(lambda f: received.append(f))

        # Push frame into mock peripheral FIFO: (id, is_extended, is_rtr, data)
        self.mock_twai.rx_frames.append((0x195B4201, True, False, b"\x11\x22\x33"))

        # Wait briefly for background RX loop
        await asyncio.sleep(0.02)

        self.assertEqual(len(received), 1)
        f = received[0]
        self.assertEqual(f.can_id, 0x195B4201)
        self.assertEqual(f.data, b"\x11\x22\x33")
        self.assertTrue(f.is_extended)

    async def test_close_deinitializes(self):
        await self.transport.close()
        self.assertFalse(self.transport.is_connected)
        self.assertTrue(self.transport.is_closed)
        self.assertTrue(self.mock_twai.deinitialized)

    async def test_node_integration(self):
        """Test LCCNode starting with Esp32TwaiTransport."""
        node = LCCNode("05.01.01.01.14.01", self.transport)
        turnout = LCCTurnout("Turnout 1", close_event="05.01.01.01.14.01.00.01", throw_event="05.01.01.01.14.01.00.02")
        node.add_component(turnout)

        # Node starts and transmits CID sequence + Initialization Complete
        # Note: in mock test without bus loopback, allocate will pick alias
        await node.start(check_delay_ms=10)
        self.assertTrue(node.is_permitted)
        self.assertGreater(len(self.mock_twai.tx_frames), 0)

        await node.stop()


if __name__ == "__main__":
    unittest.main()
