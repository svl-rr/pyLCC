"""
Unit tests for LCC CAN <-> WiFi / TCP Bridge.
"""

import unittest
import asyncio
from pylcc.can import CanFrame
from pylcc.bridge import LCCBridge
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport


class TestLccBridge(unittest.IsolatedAsyncioTestCase):
    """Test bidirectional forwarding between two transports."""

    async def asyncSetUp(self):
        # Bus 1: Represents physical CAN segment
        self.can_bus = LoopbackBus()
        self.can_transport = LoopbackTransport(self.can_bus)
        self.can_node_transport = LoopbackTransport(self.can_bus)

        # Bus 2: Represents WiFi / TCP network segment
        self.tcp_bus = LoopbackBus()
        self.tcp_transport = LoopbackTransport(self.tcp_bus)
        self.tcp_client_transport = LoopbackTransport(self.tcp_bus)

        # Bridge linking the two segments
        self.bridge = LCCBridge(
            can_transport=self.can_transport,
            tcp_transport=self.tcp_transport,
        )
        await self.bridge.start()

    async def asyncTearDown(self):
        await self.bridge.stop()
        await self.can_node_transport.close()
        await self.tcp_client_transport.close()

    async def test_can_to_tcp_forwarding(self):
        """Frame from CAN node should be bridged across to TCP client."""
        received_by_tcp = []
        self.tcp_client_transport.add_listener(lambda f: received_by_tcp.append(f))

        # CAN node transmits frame
        frame = CanFrame(can_id=0x195B4101, data=b"\x01\x02\x03\x04", is_extended=True)
        await self.can_node_transport.send_frame(frame)

        # Wait briefly for async forwarding
        await asyncio.sleep(0.02)

        self.assertEqual(len(received_by_tcp), 1)
        self.assertEqual(received_by_tcp[0].can_id, 0x195B4101)
        self.assertEqual(received_by_tcp[0].data, b"\x01\x02\x03\x04")

        stats = self.bridge.get_stats()
        self.assertEqual(stats["can_to_tcp"], 1)
        self.assertEqual(stats["tcp_to_can"], 0)

    async def test_tcp_to_can_forwarding(self):
        """Frame from TCP client (e.g. JMRI) should be bridged across to physical CAN bus."""
        received_by_can = []
        self.can_node_transport.add_listener(lambda f: received_by_can.append(f))

        # TCP client sends frame
        frame = CanFrame(can_id=0x195B4202, data=b"\xAA\xBB\xCC", is_extended=True)
        await self.tcp_client_transport.send_frame(frame)

        await asyncio.sleep(0.02)

        self.assertEqual(len(received_by_can), 1)
        self.assertEqual(received_by_can[0].can_id, 0x195B4202)
        self.assertEqual(received_by_can[0].data, b"\xAA\xBB\xCC")

        stats = self.bridge.get_stats()
        self.assertEqual(stats["tcp_to_can"], 1)

    async def test_stop_lifecycle(self):
        self.assertTrue(self.bridge.is_running)
        await self.bridge.stop()
        self.assertFalse(self.bridge.is_running)


if __name__ == "__main__":
    unittest.main()
