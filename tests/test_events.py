import asyncio
import unittest
from pylcc.node_id import NodeID
from pylcc.event_id import EventID
from pylcc.alias import AliasManager
from pylcc.events import (
    EventManager,
    EVENT_STATE_VALID,
    EVENT_STATE_INVALID,
    EVENT_STATE_UNKNOWN,
    MTI_PCER,
    MTI_IDENTIFY_CONSUMER,
    MTI_IDENTIFY_PRODUCER,
    MTI_CONSUMER_IDENTIFIED_VALID,
    MTI_CONSUMER_IDENTIFIED_UNKNOWN,
    MTI_CONSUMER_RANGE_IDENTIFIED,
    MTI_PRODUCER_IDENTIFIED_VALID,
    MTI_PRODUCER_IDENTIFIED_UNKNOWN,
    MTI_PRODUCER_RANGE_IDENTIFIED,
    MTI_IDENTIFY_EVENTS_GLOBAL,
    MTI_IDENTIFY_EVENTS_ADDRESSED,
)
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.can import CanFrame


class TestEventID(unittest.TestCase):
    def test_creation_formats(self):
        eid_str_dots = EventID("05.01.01.01.14.00.00.01")
        eid_str_colons = EventID("05:01:01:01:14:00:00:01")
        eid_int = EventID(0x0501010114000001)
        eid_bytes = EventID(b"\x05\x01\x01\x01\x14\x00\x00\x01")

        self.assertEqual(eid_str_dots, eid_str_colons)
        self.assertEqual(eid_str_dots, eid_int)
        self.assertEqual(eid_str_dots, eid_bytes)
        self.assertEqual(str(eid_str_dots), "05.01.01.01.14.00.00.01")

    def test_from_node_id(self):
        node_id = NodeID("05.01.01.01.14.00")
        eid = EventID.from_node_id(node_id, 0x002A)
        self.assertEqual(eid.node_id, node_id)
        self.assertEqual(eid.event_index, 0x002A)
        self.assertEqual(str(eid), "05.01.01.01.14.00.00.2A")

    def test_invalid_event_id(self):
        with self.assertRaises(ValueError):
            EventID(0x10000000000000000)  # > 64 bits
        with self.assertRaises(ValueError):
            EventID(b"\x01\x02\x03")  # Not 8 bytes
        with self.assertRaises(ValueError):
            EventID("05.01.01")  # Bad string

    def test_encode_and_decode_range(self):
        # Range of 16 events starting at ...00.00 (bit 4 is 0 -> low 4 bits become 1)
        rng0 = EventID.encode_range("05.01.01.01.14.01.00.00", 16)
        self.assertEqual(str(rng0), "05.01.01.01.14.01.00.0F")
        base, size, mask = EventID.decode_range(rng0)
        self.assertEqual(str(base), "05.01.01.01.14.01.00.00")
        self.assertEqual(size, 16)
        self.assertEqual(mask, 0x0F)

        # Range of 16 events starting at ...00.10 (bit 4 is 1 -> low 4 bits become 0)
        rng1 = EventID.encode_range("05.01.01.01.14.01.00.10", 16)
        self.assertEqual(str(rng1), "05.01.01.01.14.01.00.10")
        base1, size1, mask1 = EventID.decode_range(rng1)
        self.assertEqual(str(base1), "05.01.01.01.14.01.00.10")
        self.assertEqual(size1, 16)
        self.assertEqual(mask1, 0x0F)

        with self.assertRaises(ValueError):
            EventID.encode_range("05.01.01.01.14.01.00.00", 10)  # Not power of 2


class TestEventManager(unittest.IsolatedAsyncioTestCase):
    async def test_produce_and_consume(self):
        bus = LoopbackBus()
        t_producer = LoopbackTransport(bus)
        t_consumer = LoopbackTransport(bus)

        alias_mgr_prod = AliasManager("05.01.01.01.14.01", t_producer)
        alias_mgr_cons = AliasManager("05.01.01.01.14.02", t_consumer)

        await alias_mgr_prod.allocate(check_delay_ms=10)
        await alias_mgr_cons.allocate(check_delay_ms=10)

        events_prod = EventManager("05.01.01.01.14.01", alias_mgr_prod, t_producer)
        events_cons = EventManager("05.01.01.01.14.02", alias_mgr_cons, t_consumer)

        # Hook frame reception
        t_consumer.add_listener(lambda f: asyncio.create_task(events_cons.process_frame(f)))
        t_producer.add_listener(lambda f: asyncio.create_task(events_prod.process_frame(f)))

        event_turnout_thrown = EventID("05.01.01.01.14.01.00.01")
        consumed_events = []

        # Register consumer with a callback
        events_cons.register_consumer(event_turnout_thrown, callback=lambda e: consumed_events.append(e))
        events_prod.register_producer(event_turnout_thrown)

        # Produce event from Node 1
        await events_prod.produce(event_turnout_thrown)
        await asyncio.sleep(0.05)

        self.assertEqual(len(consumed_events), 1)
        self.assertEqual(consumed_events[0], event_turnout_thrown)

        await t_producer.close()
        await t_consumer.close()

    async def test_identify_queries(self):
        bus = LoopbackBus()
        t_node = LoopbackTransport(bus)
        t_tester = LoopbackTransport(bus)

        alias_mgr = AliasManager("05.01.01.01.14.01", t_node)
        await alias_mgr.allocate(check_delay_ms=10)

        event_mgr = EventManager("05.01.01.01.14.01", alias_mgr, t_node)
        t_node.add_listener(lambda f: asyncio.create_task(event_mgr.process_frame(f)))

        e_btn = EventID("05.01.01.01.14.01.00.01")
        e_led = EventID("05.01.01.01.14.01.00.02")

        event_mgr.register_producer(e_btn, state=EVENT_STATE_VALID)
        event_mgr.register_consumer(e_led, state=EVENT_STATE_VALID)

        tester_rx = []
        t_tester.add_listener(lambda f: tester_rx.append(f))

        # 1. Tester queries Identify Consumer for e_led
        query_frame = CanFrame.make_global_message(MTI_IDENTIFY_CONSUMER, 0x999, e_led.to_bytes())
        await t_tester.send_frame(query_frame)
        await asyncio.sleep(0.05)

        self.assertEqual(len(tester_rx), 1)
        resp = tester_rx[0]
        self.assertEqual(resp.mti, MTI_CONSUMER_IDENTIFIED_VALID)
        self.assertEqual(resp.data, e_led.to_bytes())
        self.assertEqual(resp.source_alias, alias_mgr.alias)

        # 2. Tester queries Identify Producer for e_btn
        tester_rx.clear()
        query_frame = CanFrame.make_global_message(MTI_IDENTIFY_PRODUCER, 0x999, e_btn.to_bytes())
        await t_tester.send_frame(query_frame)
        await asyncio.sleep(0.05)

        self.assertEqual(len(tester_rx), 1)
        resp = tester_rx[0]
        self.assertEqual(resp.mti, MTI_PRODUCER_IDENTIFIED_VALID)
        self.assertEqual(resp.data, e_btn.to_bytes())

        # 3. Tester sends Identify Events Global
        tester_rx.clear()
        query_global = CanFrame.make_global_message(MTI_IDENTIFY_EVENTS_GLOBAL, 0x999)
        await t_tester.send_frame(query_global)
        await asyncio.sleep(0.05)

        # Node should reply for both e_led (consumer) and e_btn (producer)
        self.assertEqual(len(tester_rx), 2)
        mtis = {f.mti for f in tester_rx}
        self.assertIn(MTI_CONSUMER_IDENTIFIED_VALID, mtis)
        self.assertIn(MTI_PRODUCER_IDENTIFIED_VALID, mtis)

        # 4. Tester sends Identify Events Addressed
        tester_rx.clear()
        query_addressed = CanFrame.make_addressed_message(
            MTI_IDENTIFY_EVENTS_ADDRESSED,
            source_alias=0x999,
            dest_alias=alias_mgr.alias
        )
        await t_tester.send_frame(query_addressed)
        await asyncio.sleep(0.05)

        self.assertEqual(len(tester_rx), 2)

        await t_node.close()
        await t_tester.close()

    async def test_consumer_and_producer_ranges(self):
        """Test registering power-of-two Consumer/Producer ranges and Range Identified frames."""
        bus = LoopbackBus()
        t_node = LoopbackTransport(bus)
        t_tester = LoopbackTransport(bus)

        alias_mgr = AliasManager("05.01.01.01.14.01", t_node)
        await alias_mgr.allocate(check_delay_ms=10)

        event_mgr = EventManager("05.01.01.01.14.01", alias_mgr, t_node)
        t_node.add_listener(lambda f: asyncio.create_task(event_mgr.process_frame(f)))

        range_events_rx = []
        event_mgr.register_consumer_range(
            "05.01.01.01.14.01.01.00",
            16,
            callback=lambda eid: range_events_rx.append(eid),
        )
        event_mgr.register_producer_range("05.01.01.01.14.01.02.00", 8)

        self.assertTrue(event_mgr.is_consumer("05.01.01.01.14.01.01.05"))
        self.assertFalse(event_mgr.is_consumer("05.01.01.01.14.01.01.10"))
        self.assertTrue(event_mgr.is_producer("05.01.01.01.14.01.02.07"))
        self.assertFalse(event_mgr.is_producer("05.01.01.01.14.01.02.08"))

        tester_rx = []
        t_tester.add_listener(lambda f: tester_rx.append(f))

        # 1. PCER matching inside consumer range triggers callback
        pcer_in = CanFrame.make_global_message(
            MTI_PCER,
            0x999,
            EventID("05.01.01.01.14.01.01.0A").to_bytes(),
        )
        pcer_out = CanFrame.make_global_message(
            MTI_PCER,
            0x999,
            EventID("05.01.01.01.14.01.01.10").to_bytes(),
        )
        self.assertTrue(event_mgr.has_interest(pcer_in))
        self.assertFalse(event_mgr.has_interest(pcer_out))
        await t_tester.send_frame(pcer_in)
        await t_tester.send_frame(pcer_out)
        await asyncio.sleep(0.05)

        self.assertEqual(len(range_events_rx), 1)
        self.assertEqual(range_events_rx[0], EventID("05.01.01.01.14.01.01.0A"))

        # 2. Identify Consumer/Producer matching inside ranges responds
        tester_rx.clear()
        await t_tester.send_frame(
            CanFrame.make_global_message(
                MTI_IDENTIFY_CONSUMER,
                0x999,
                EventID("05.01.01.01.14.01.01.03").to_bytes(),
            )
        )
        await t_tester.send_frame(
            CanFrame.make_global_message(
                MTI_IDENTIFY_PRODUCER,
                0x999,
                EventID("05.01.01.01.14.01.02.04").to_bytes(),
            )
        )
        await asyncio.sleep(0.05)
        self.assertEqual(
            {f.mti for f in tester_rx},
            {MTI_CONSUMER_IDENTIFIED_UNKNOWN, MTI_PRODUCER_IDENTIFIED_UNKNOWN},
        )

        # 3. Identify Events Global emits Consumer Range Identified (0x04A4) & Producer Range Identified (0x0524)
        tester_rx.clear()
        await t_tester.send_frame(CanFrame.make_global_message(MTI_IDENTIFY_EVENTS_GLOBAL, 0x999))
        await asyncio.sleep(0.05)

        mti_map = {f.mti: f.data for f in tester_rx}
        self.assertIn(MTI_CONSUMER_RANGE_IDENTIFIED, mti_map)
        self.assertIn(MTI_PRODUCER_RANGE_IDENTIFIED, mti_map)
        self.assertEqual(EventID(mti_map[MTI_CONSUMER_RANGE_IDENTIFIED]), EventID("05.01.01.01.14.01.01.0F"))
        self.assertEqual(EventID(mti_map[MTI_PRODUCER_RANGE_IDENTIFIED]), EventID("05.01.01.01.14.01.02.07"))

        await t_node.close()
        await t_tester.close()


if __name__ == "__main__":
    unittest.main()
