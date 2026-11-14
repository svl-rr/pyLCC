"""
Unit tests verifying performance & memory optimizations across pyLCC.
"""

import asyncio
import struct
import unittest
from unittest.mock import patch

from pylcc.can import CanFrame
from pylcc.node_id import NodeID
from pylcc.event_id import EventID
from pylcc.events import EventManager, MTI_PCER
from pylcc.alias import AliasManager
from pylcc.datagram import DatagramManager
from pylcc.gridconnect import GridConnectParser, decode_gridconnect, encode_gridconnect
from pylcc.memory import BytesMemorySpace, SPACE_CDI
from pylcc.node import LCCNode
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.hardware.mcp2518fd import (
    Mcp2518fdTransport,
    REG_CiFIFOCON1,
    REG_CiFIFOSTA1,
    REG_CiFIFOUA1,
    RAM_START_ADDR,
)
from tests.test_mcp2518fd import MockSpiController, MockCsPin


class MockIntPin:
    """Mock active-low MCP2518FD interrupt pin (0 = asserted, 1 = idle)."""
    def __init__(self, initial=1):
        self.state = initial
        self.reads = 0

    def value(self, val=None):
        if val is not None:
            self.state = val
        else:
            self.reads += 1
        return self.state


class BurstMockSpiController(MockSpiController):
    """Mock SPI controller simulating a multi-frame hardware RX FIFO queue."""
    def __init__(self):
        super().__init__()
        self.pending_frames = []
        self.sta_reads = 0
        self.fifocon_reads = 0

    def push_rx_frame(self, can_id: int, payload: bytes):
        self.pending_frames.append((can_id, payload))
        self.registers[REG_CiFIFOSTA1] = 0x01
        self._load_head_into_ram()

    def _load_head_into_ram(self):
        if not self.pending_frames:
            self.registers[REG_CiFIFOSTA1] = 0x00
            return
        can_id, payload = self.pending_frames[0]
        sid = (can_id >> 18) & 0x7FF
        eid = can_id & 0x3FFFF
        word0 = (eid << 11) | sid
        word1 = (1 << 4) | len(payload)
        padded = payload + b"\x00" * (8 - len(payload))
        offset = self.registers[REG_CiFIFOUA1]
        self.ram[offset:offset + 8] = struct.pack("<II", word0, word1)
        self.ram[offset + 8:offset + 16] = padded[:8]
        self.registers[REG_CiFIFOSTA1] = 0x01

    def write(self, data: bytes):
        if len(data) == 4 and self._current_cmd == 0x02:
            val = struct.unpack("<I", data)[0]
            if self._current_addr == REG_CiFIFOCON1 and (val & (1 << 8)):
                if self.pending_frames:
                    self.pending_frames.pop(0)
                self.registers[REG_CiFIFOCON1] = val
                self._load_head_into_ram()
                return
        super().write(data)

    def read(self, length: int) -> bytes:
        if self._current_cmd == 0x03:
            if self._current_addr == REG_CiFIFOSTA1:
                self.sta_reads += 1
            elif self._current_addr == REG_CiFIFOCON1:
                self.fifocon_reads += 1
        return super().read(length)


class TestOptimizations(unittest.IsolatedAsyncioTestCase):
    def test_cached_node_id_and_event_id_bytes(self):
        nid = NodeID("05.01.01.01.14.01")
        b1 = nid.to_bytes()
        b2 = nid.to_bytes()
        self.assertIs(b1, b2)
        self.assertEqual(b1, b"\x05\x01\x01\x01\x14\x01")

        eid = EventID("05.01.01.01.14.01.00.09")
        eb1 = eid.to_bytes()
        eb2 = eid.to_bytes()
        self.assertIs(eb1, eb2)
        self.assertEqual(eb1, b"\x05\x01\x01\x01\x14\x01\x00\x09")

    def test_readonly_bytes_memory_space_zero_copy(self):
        xml_bytes = b"<cdi>test</cdi>\x00"
        space = BytesMemorySpace(SPACE_CDI, xml_bytes, read_only=True)
        self.assertIs(space._data, xml_bytes)
        self.assertEqual(space.read(0, 5), b"<cdi>")

    def test_gridconnect_whitespace_and_hex(self):
        frame = CanFrame(0x195B422A, b"\x0A\xBC\xDE\xF0")
        encoded = encode_gridconnect(frame)
        self.assertEqual(encoded, ":X195B422AN0ABCDEF0;")
        decoded = decode_gridconnect(encoded)
        self.assertEqual(decoded, frame)

        parser = GridConnectParser()
        frames = parser.feed(b"  :X 195B422AN0A BC; :X10700123N;\r\n")
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].data, b"\x0A\xBC")
        self.assertEqual(frames[1].can_id, 0x10700123)

    async def test_rx_queue_no_memory_leak_with_listeners(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        t2 = LoopbackTransport(bus)

        received = []
        t2.add_listener(lambda f: received.append(f))

        for i in range(50):
            await t1.send_frame(CanFrame(0x195B4101, bytes([i])))

        self.assertEqual(len(received), 50)
        self.assertEqual(t2._rx_queue.qsize(), 0)

        await t1.close()
        await t2.close()

    async def test_rx_queue_bounded_when_unread(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        t2 = LoopbackTransport(bus)

        for i in range(60):
            await t1.send_frame(CanFrame(0x195B4101, bytes([i])))

        self.assertEqual(t2._rx_queue.qsize(), 32)
        oldest = await t2.receive_frame()
        self.assertEqual(oldest.data, bytes([28]))

        await t1.close()
        await t2.close()

    async def test_datagram_stale_buffer_eviction(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        alias1 = AliasManager("05.01.01.01.14.01", t1)
        await alias1.allocate(check_delay_ms=10)

        dm = DatagramManager("05.01.01.01.14.01", alias1, t1, reassembly_timeout_ms=100)

        # Simulate a dropped multi-frame datagram (First frame arrives, Final never arrives)
        first_frame = CanFrame.make_datagram_first(0x777, alias1.alias, b"\x20\x41\x00\x00\x00\x00\xFF\x10")
        await dm.process_frame(first_frame)
        self.assertIn(0x777, dm._rx_buffers)

        # Advance time beyond timeout and evict
        evicted = dm.evict_stale_buffers(now_ms=dm._rx_timestamps[0x777] + 150)
        self.assertEqual(evicted, [0x777])
        self.assertNotIn(0x777, dm._rx_buffers)
        self.assertNotIn(0x777, dm._rx_timestamps)

        await t1.close()

    async def test_node_fast_path_skips_irrelevant_pcer_tasks(self):
        bus = LoopbackBus()
        t_node = LoopbackTransport(bus)
        t_other = LoopbackTransport(bus)

        node = LCCNode("05.01.01.01.14.01", t_node)
        await node.start(check_delay_ms=10)

        irrelevant_frame = CanFrame.make_global_message(
            MTI_PCER,
            source_alias=0x555,
            data=b"\x01\x02\x03\x04\x05\x06\x07\x08",
        )
        with patch("pylcc.node.asyncio.create_task") as mock_create_task:
            await t_other.send_frame(irrelevant_frame)
            mock_create_task.assert_not_called()

        await node.stop()
        await t_other.close()

    async def test_mcp2518fd_int_pin_and_burst_drain(self):
        spi = BurstMockSpiController()
        cs = MockCsPin()
        int_pin = MockIntPin(initial=1)  # Idle high

        transport = Mcp2518fdTransport(
            spi=spi,
            cs_pin=cs,
            int_pin=int_pin,
            osc_freq_mhz=20,
            baudrate=125000,
        )
        await transport.connect()

        received = []
        transport.add_listener(lambda f: received.append(f))

        # Idle INT pin should prevent SPI polling of CiFIFOSTA1
        await asyncio.sleep(0.015)
        self.assertGreater(int_pin.reads, 0)
        self.assertEqual(spi.sta_reads, 0)

        # Push 5 burst frames and assert active-low INT pin
        for i in range(5):
            spi.push_rx_frame(0x195B4300 + i, bytes([i]))
        int_pin.state = 0

        await asyncio.sleep(0.015)
        int_pin.state = 1

        self.assertEqual(len(received), 5)
        self.assertEqual([f.data[0] for f in received], [0, 1, 2, 3, 4])
        # Cached _fifo1con avoids re-reading CiFIFOCON1 inside _rx_loop
        self.assertEqual(spi.fifocon_reads, 0)

        await transport.close()


if __name__ == "__main__":
    unittest.main()
