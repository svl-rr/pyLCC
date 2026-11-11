import asyncio
import unittest
from pylcc.node_id import NodeID
from pylcc.alias import AliasManager
from pylcc.datagram import DatagramManager, Datagram, DATAGRAM_MAX_SIZE
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport


class TestDatagram(unittest.IsolatedAsyncioTestCase):
    async def test_single_frame_datagram(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        t2 = LoopbackTransport(bus)

        alias1 = AliasManager("05.01.01.01.14.01", t1)
        alias2 = AliasManager("05.01.01.01.14.02", t2)
        await alias1.allocate(check_delay_ms=10)
        await alias2.allocate(check_delay_ms=10)

        dm1 = DatagramManager("05.01.01.01.14.01", alias1, t1)
        dm2 = DatagramManager("05.01.01.01.14.02", alias2, t2)

        t1.add_listener(lambda f: asyncio.create_task(dm1.process_frame(f)))
        t2.add_listener(lambda f: asyncio.create_task(dm2.process_frame(f)))

        received_datagrams = []

        def on_datagram(dg: Datagram):
            received_datagrams.append(dg)
            return True

        dm2.register_handler(0x20, on_datagram)

        payload = b"\x20\x01\x02\x03\x04"
        success = await dm1.send_datagram(alias2.alias, payload)
        self.assertTrue(success)

        self.assertEqual(len(received_datagrams), 1)
        dg = received_datagrams[0]
        self.assertEqual(dg.data, payload)
        self.assertEqual(dg.source_alias, alias1.alias)
        self.assertEqual(dg.dest_alias, alias2.alias)
        self.assertEqual(dg.protocol_id, 0x20)

        await t1.close()
        await t2.close()

    async def test_multi_frame_datagram(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        t2 = LoopbackTransport(bus)

        alias1 = AliasManager("05.01.01.01.14.01", t1)
        alias2 = AliasManager("05.01.01.01.14.02", t2)
        await alias1.allocate(check_delay_ms=10)
        await alias2.allocate(check_delay_ms=10)

        dm1 = DatagramManager("05.01.01.01.14.01", alias1, t1)
        dm2 = DatagramManager("05.01.01.01.14.02", alias2, t2)

        t1.add_listener(lambda f: asyncio.create_task(dm1.process_frame(f)))
        t2.add_listener(lambda f: asyncio.create_task(dm2.process_frame(f)))

        received_datagrams = []
        dm2.register_handler(None, lambda dg: received_datagrams.append(dg) or True)

        # 42 bytes payload (spans 6 CAN frames)
        payload = bytes([i % 256 for i in range(42)])
        success = await dm1.send_datagram(alias2.alias, payload)
        self.assertTrue(success)

        self.assertEqual(len(received_datagrams), 1)
        self.assertEqual(received_datagrams[0].data, payload)

        await t1.close()
        await t2.close()

    async def test_rejected_datagram(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        t2 = LoopbackTransport(bus)

        alias1 = AliasManager("05.01.01.01.14.01", t1)
        alias2 = AliasManager("05.01.01.01.14.02", t2)
        await alias1.allocate(check_delay_ms=10)
        await alias2.allocate(check_delay_ms=10)

        dm1 = DatagramManager("05.01.01.01.14.01", alias1, t1)
        dm2 = DatagramManager("05.01.01.01.14.02", alias2, t2)

        t1.add_listener(lambda f: asyncio.create_task(dm1.process_frame(f)))
        t2.add_listener(lambda f: asyncio.create_task(dm2.process_frame(f)))

        # Handler returns False -> reject datagram
        dm2.register_handler(0x20, lambda dg: False)

        success = await dm1.send_datagram(alias2.alias, b"\x20\xFF")
        self.assertFalse(success)

        await t1.close()
        await t2.close()

    async def test_datagram_max_size(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        alias1 = AliasManager("05.01.01.01.14.01", t1)
        dm = DatagramManager("05.01.01.01.14.01", alias1, t1)

        with self.assertRaises(ValueError):
            await dm.send_datagram(0x123, bytes(DATAGRAM_MAX_SIZE + 1))


if __name__ == "__main__":
    unittest.main()
