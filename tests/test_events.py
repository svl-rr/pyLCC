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
    MTI_PRODUCER_IDENTIFIED_VALID,
    MTI_CONSUMER_IDENTIFIED_UNKNOWN,
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


if __name__ == "__main__":
    unittest.main()
