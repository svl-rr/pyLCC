import asyncio
import unittest
from pylcc.node_id import NodeID
from pylcc.alias import AliasPrng, AliasManager, STATE_INHIBITED, STATE_CHECKING, STATE_PERMITTED
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.can import CanFrame


class TestNodeID(unittest.TestCase):
    def test_creation_formats(self):
        nid_str_dots = NodeID("05.01.01.01.14.00")
        nid_str_colons = NodeID("05:01:01:01:14:00")
        nid_str_plain = NodeID("050101011400")
        nid_int = NodeID(0x050101011400)
        nid_bytes = NodeID(b"\x05\x01\x01\x01\x14\x00")

        self.assertEqual(nid_str_dots, nid_str_colons)
        self.assertEqual(nid_str_dots, nid_str_plain)
        self.assertEqual(nid_str_dots, nid_int)
        self.assertEqual(nid_str_dots, nid_bytes)
        self.assertEqual(nid_str_dots.to_bytes(), b"\x05\x01\x01\x01\x14\x00")
        self.assertEqual(str(nid_str_dots), "05.01.01.01.14.00")

    def test_chunks(self):
        # 0x050101011400:
        # Bits 47-36: 0x050
        # Bits 35-24: 0x101
        # Bits 23-12: 0x011
        # Bits 11-0:  0x400
        nid = NodeID("05.01.01.01.14.00")
        self.assertEqual(nid.chunk_7, 0x050)
        self.assertEqual(nid.chunk_6, 0x101)
        self.assertEqual(nid.chunk_5, 0x011)
        self.assertEqual(nid.chunk_4, 0x400)
        self.assertEqual(nid.get_chunk(7), 0x050)
        self.assertEqual(nid.get_chunk(4), 0x400)

    def test_invalid_values(self):
        with self.assertRaises(ValueError):
            NodeID(0x1000000000000)  # > 48 bits
        with self.assertRaises(ValueError):
            NodeID(b"\x01\x02\x03")  # Not 6 bytes
        with self.assertRaises(ValueError):
            NodeID("05.01.01")  # Malformed string


class TestAliasPrng(unittest.TestCase):
    def test_prng_sequence(self):
        prng = AliasPrng(0x050101011400)
        alias1 = prng.next_alias()
        alias2 = prng.next_alias()
        self.assertNotEqual(alias1, 0)
        self.assertNotEqual(alias2, 0)
        self.assertTrue(1 <= alias1 <= 0xFFF)
        self.assertTrue(1 <= alias2 <= 0xFFF)

        # Ensure deterministic output for same seed
        prng_copy = AliasPrng(0x050101011400)
        self.assertEqual(prng_copy.next_alias(), alias1)
        self.assertEqual(prng_copy.next_alias(), alias2)


class TestAliasManager(unittest.IsolatedAsyncioTestCase):
    async def test_normal_allocation(self):
        bus = LoopbackBus()
        node_transport = LoopbackTransport(bus)
        monitor_transport = LoopbackTransport(bus)

        frames_seen = []
        monitor_transport.add_listener(lambda f: frames_seen.append(f))

        manager = AliasManager("05.01.01.01.14.00", node_transport)
        node_transport.add_listener(lambda f: asyncio.create_task(manager.process_frame(f)))

        # Allocate with a fast 20ms check delay for tests
        alias = await manager.allocate(check_delay_ms=20)
        self.assertTrue(manager.is_permitted)
        self.assertEqual(manager.state, STATE_PERMITTED)
        self.assertEqual(manager.alias, alias)

        # Inspect frames transmitted to monitor:
        # Expected: 4 CID frames, 1 RID frame, 1 AMD frame
        self.assertEqual(len(frames_seen), 6)
        cids = frames_seen[:4]
        for idx, seq in enumerate((7, 6, 5, 4)):
            self.assertTrue(cids[idx].is_cid)
            self.assertEqual(cids[idx].cid_sequence, seq)
            self.assertEqual(cids[idx].source_alias, alias)

        rid = frames_seen[4]
        self.assertTrue(rid.is_rid)
        self.assertEqual(rid.source_alias, alias)

        amd = frames_seen[5]
        self.assertTrue(amd.is_amd)
        self.assertEqual(amd.source_alias, alias)
        self.assertEqual(amd.data, NodeID("05.01.01.01.14.00").to_bytes())

        await node_transport.close()
        await monitor_transport.close()

    async def test_collision_during_check(self):
        bus = LoopbackBus()
        node_transport = LoopbackTransport(bus)
        antagonist_transport = LoopbackTransport(bus)

        manager = AliasManager("05.01.01.01.14.00", node_transport)
        node_transport.add_listener(lambda f: asyncio.create_task(manager.process_frame(f)))

        collision_injected = False

        def on_frame_from_manager(frame):
            nonlocal collision_injected
            # Once we see CID 7 from the manager, simulate another node claiming the same alias
            if frame.is_cid and not collision_injected:
                collision_injected = True
                # Antagonist sends RID to object to this alias
                rid = CanFrame.make_rid(frame.source_alias)
                asyncio.create_task(antagonist_transport.send_frame(rid))

        antagonist_transport.add_listener(on_frame_from_manager)

        # Allocate with a 50ms delay
        alias = await manager.allocate(check_delay_ms=50)
        self.assertTrue(collision_injected)
        self.assertTrue(manager.is_permitted)
        # Should have stepped to a new alias
        self.assertEqual(manager.alias, alias)

        await node_transport.close()
        await antagonist_transport.close()

    async def test_active_defense_and_ame(self):
        bus = LoopbackBus()
        node_transport = LoopbackTransport(bus)
        peer_transport = LoopbackTransport(bus)

        manager = AliasManager("05.01.01.01.14.00", node_transport)
        node_transport.add_listener(lambda f: asyncio.create_task(manager.process_frame(f)))

        alias = await manager.allocate(check_delay_ms=20)
        self.assertTrue(manager.is_permitted)

        peer_received = []
        peer_transport.add_listener(lambda f: peer_received.append(f))

        # 1. Newcomer sends CID on our alias -> Manager must defend with RID!
        newcomer_cid = CanFrame.make_cid(7, 0x123, alias)
        await peer_transport.send_frame(newcomer_cid)
        await asyncio.sleep(0.05)

        self.assertTrue(any(f.is_rid and f.source_alias == alias for f in peer_received))

        # 2. Peer sends AME (Alias Mapping Enquiry) -> Manager must respond with AMD!
        peer_received.clear()
        ame = CanFrame.make_ame(0x777)
        await peer_transport.send_frame(ame)
        await asyncio.sleep(0.05)

        self.assertTrue(any(f.is_amd and f.source_alias == alias for f in peer_received))

        # 3. Release alias
        peer_received.clear()
        await manager.release()
        await asyncio.sleep(0.05)
        self.assertTrue(any(f.is_amr and f.source_alias == alias for f in peer_received))
        self.assertEqual(manager.state, STATE_INHIBITED)

        await node_transport.close()
        await peer_transport.close()


if __name__ == "__main__":
    unittest.main()
