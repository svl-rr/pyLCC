"""
Unit tests for pyLCC Unified Node Orchestrator and Layout Components.
"""

import unittest
import asyncio
import os
import tempfile
from pylcc.node import (
    Node,
    LCCNode,
    MTI_VERIFY_NODE_ID_GLOBAL,
    MTI_VERIFY_NODE_ID_ADDRESSED,
    MTI_VERIFIED_NODE_ID,
    MTI_OPTIONAL_INTERACTION_REJECTED,
)
from pylcc.node_id import NodeID
from pylcc.event_id import EventID
from pylcc.can import CanFrame
from pylcc.events import MTI_PCER, MTI_IDENTIFY_EVENTS_ADDRESSED
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.components import (
    Turnout,
    Sensor,
    Signal,
    Output,
    LCCTurnout,
    LCCSensor,
    LCCSignal,
    LCCOutput,
)


class TestNodeAndComponents(unittest.IsolatedAsyncioTestCase):
    """Test the complete Node stack with Turnout, Sensor, Signal, and Output."""

    async def asyncSetUp(self):
        self.bus = LoopbackBus()
        self.t1 = LoopbackTransport(self.bus)
        self.t2 = LoopbackTransport(self.bus)

        # Node 1: Main pyLCC Node
        self.node = Node("05.01.01.01.14.01", self.t1)

        # Node 2: Remote peer / monitor (simulating JMRI or layout node)
        self.peer_node = Node("02.01.12.00.00.01", self.t2)

    async def asyncTearDown(self):
        await self.node.stop()
        await self.peer_node.stop()

    async def test_node_start_and_alias_allocation(self):
        """Test node startup, alias acquisition, and Initialization Complete broadcast."""
        init_frames = []
        self.t2.add_listener(lambda f: init_frames.append(f) if f.mti == 0x0100 else None)

        await self.node.start(check_delay_ms=10)

        self.assertTrue(self.node.is_permitted)
        self.assertNotEqual(self.node.alias, 0)

        # Wait briefly for frame delivery
        await asyncio.sleep(0.02)
        self.assertEqual(len(init_frames), 1)
        self.assertEqual(init_frames[0].mti, 0x0100)
        self.assertEqual(init_frames[0].data, self.node.node_id.to_bytes())

    async def test_turnout_component(self):
        """Test Turnout reacting to events and producing feedback."""
        changes = []
        turnout = Turnout(
            name="Turnout 1",
            close_event="05.01.01.01.14.01.00.01",
            throw_event="05.01.01.01.14.01.00.02",
            is_closed_event="05.01.01.01.14.01.00.03",
            is_thrown_event="05.01.01.01.14.01.00.04",
            initial_state=Turnout.STATE_CLOSED,
            on_change=lambda state: changes.append(state),
        )
        self.node.add_component(turnout)
        self.assertEqual(self.node.get_component("Turnout 1"), turnout)

        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        feedback_received = []
        self.peer_node.register_consumer("05.01.01.01.14.01.00.04", lambda eid: feedback_received.append(eid))

        # Peer sends THROW command event
        await self.peer_node.produce_event("05.01.01.01.14.01.00.02")
        await asyncio.sleep(0.05)

        # Turnout should have transitioned to THROWN
        self.assertEqual(turnout.state, Turnout.STATE_THROWN)
        self.assertIn(Turnout.STATE_THROWN, changes)

        # Peer should have received feedback event is_thrown (05.01.01.01.14.01.00.04)
        self.assertEqual(len(feedback_received), 1)
        self.assertEqual(feedback_received[0], EventID("05.01.01.01.14.01.00.04"))

        # Local command: close()
        await turnout.close()
        self.assertEqual(turnout.state, Turnout.STATE_CLOSED)

    async def test_sensor_component(self):
        """Test Sensor producing events when state changes."""
        changes = []
        sensor = Sensor(
            name="Block Occupancy",
            active_event="05.01.01.01.14.01.00.10",
            inactive_event="05.01.01.01.14.01.00.11",
            initial_state=Sensor.STATE_INACTIVE,
            on_change=lambda state: changes.append(state),
        )
        self.node.add_component(sensor)

        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        peer_received = []
        self.peer_node.register_consumer("05.01.01.01.14.01.00.10", lambda eid: peer_received.append("OCCUPIED"))
        self.peer_node.register_consumer("05.01.01.01.14.01.00.11", lambda eid: peer_received.append("CLEARED"))

        # Train enters block
        await sensor.set_active(True)
        await asyncio.sleep(0.05)

        self.assertTrue(sensor.is_active)
        self.assertEqual(peer_received, ["OCCUPIED"])
        self.assertEqual(changes, [Sensor.STATE_ACTIVE])

        # Train leaves block
        await sensor.set_active(False)
        await asyncio.sleep(0.05)

        self.assertFalse(sensor.is_active)
        self.assertEqual(peer_received, ["OCCUPIED", "CLEARED"])
        self.assertEqual(changes, [Sensor.STATE_ACTIVE, Sensor.STATE_INACTIVE])

    async def test_signal_component(self):
        """Test Signal mast aspect transitions."""
        aspects_received = []
        signal = Signal(
            name="Signal 2W",
            aspect_events={
                "STOP": "05.01.01.01.14.01.00.20",
                "APPROACH": "05.01.01.01.14.01.00.21",
                "CLEAR": "05.01.01.01.14.01.00.22",
            },
            initial_aspect="STOP",
            on_change=lambda asp: aspects_received.append(asp),
        )
        self.node.add_component(signal)

        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        # Peer broadcasts CLEAR aspect
        await self.peer_node.produce_event("05.01.01.01.14.01.00.22")
        await asyncio.sleep(0.05)

        self.assertEqual(signal.current_aspect, "CLEAR")
        self.assertEqual(aspects_received, ["CLEAR"])

        # Peer broadcasts APPROACH aspect
        await self.peer_node.produce_event("05.01.01.01.14.01.00.21")
        await asyncio.sleep(0.05)

        self.assertEqual(signal.current_aspect, "APPROACH")
        self.assertEqual(aspects_received, ["CLEAR", "APPROACH"])

    async def test_output_component(self):
        """Test Output / Light switching ON and OFF."""
        states = []
        light = Output(
            name="Yard Floodlight",
            on_event="05.01.01.01.14.01.00.30",
            off_event="05.01.01.01.14.01.00.31",
            initial_state=Output.STATE_OFF,
            on_change=lambda st: states.append(st),
        )
        self.node.add_component(light)

        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        # Turn ON
        await self.peer_node.produce_event("05.01.01.01.14.01.00.30")
        await asyncio.sleep(0.05)
        self.assertTrue(light.is_on)
        self.assertEqual(states, [Output.STATE_ON])

        # Turn OFF
        await self.peer_node.produce_event("05.01.01.01.14.01.00.31")
        await asyncio.sleep(0.05)
        self.assertFalse(light.is_on)
        self.assertEqual(states, [Output.STATE_ON, Output.STATE_OFF])

    async def test_auto_cdi_generation(self):
        """Test that adding components auto-generates valid CDI XML served on Space 0xFF."""
        turnout = Turnout("Main Switch", close_event="05.01.01.01.14.01.00.01", throw_event="05.01.01.01.14.01.00.02")
        sensor = Sensor("Track Detector", active_event="05.01.01.01.14.01.00.10", inactive_event="05.01.01.01.14.01.00.11")

        self.node.add_component(turnout)
        self.node.add_component(sensor)

        await self.node.start(check_delay_ms=10)

        # CDI document should have been created
        self.assertIsNotNone(self.node.cdi_doc)
        cdi_xml = self.node.cdi_doc.to_xml()

        self.assertIn("<cdi", cdi_xml)
        self.assertIn("Main Switch", cdi_xml)
        self.assertIn("Track Detector", cdi_xml)
        self.assertIn("05.01.01.01.14.01.00.01", cdi_xml)
        self.assertIn("05.01.01.01.14.01.00.10", cdi_xml)

        # CDI space 0xFF should be populated
        cdi_space = self.node.memory_service.get_space(0xFF)
        self.assertIsNotNone(cdi_space)
        data = cdi_space.read(0, 32)
        self.assertTrue(data.startswith(b"<?xml"))

    def test_lcc_prefixed_aliases(self):
        """Verify LCC-prefixed aliases are identical to base classes."""
        self.assertIs(LCCNode, Node)
        self.assertIs(LCCTurnout, Turnout)
        self.assertIs(LCCSensor, Sensor)
        self.assertIs(LCCSignal, Signal)
        self.assertIs(LCCOutput, Output)

        # Test instantiating with LCC prefix
        node = LCCNode("05.01.01.01.14.01", self.t1)
        self.assertIsInstance(node, Node)
        t = LCCTurnout("Turnout A", close_event="05.01.01.01.14.01.00.01", throw_event="05.01.01.01.14.01.00.02")
        self.assertIsInstance(t, Turnout)
        s = LCCSensor("Sensor A", active_event="05.01.01.01.14.01.00.10", inactive_event="05.01.01.01.14.01.00.11")
        self.assertIsInstance(s, Sensor)

    async def test_verify_node_id_global_and_addressed(self):
        """Test Verify Node ID Global (0x0490) and Addressed (0x0488) replies with 0x0170."""
        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        verified_frames = []
        self.t2.add_listener(
            lambda f: verified_frames.append(f) if f.mti == MTI_VERIFIED_NODE_ID else None
        )

        # 1. Global empty Verify Node ID -> should trigger Verified Node ID
        await self.t2.send_frame(
            CanFrame.make_global_message(MTI_VERIFY_NODE_ID_GLOBAL, self.peer_node.alias, b"")
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(verified_frames), 1)
        self.assertEqual(verified_frames[0].source_alias, self.node.alias)
        self.assertEqual(verified_frames[0].data, self.node.node_id.to_bytes())

        # 2. Global Verify Node ID with matching NodeID -> replies
        verified_frames.clear()
        await self.t2.send_frame(
            CanFrame.make_global_message(
                MTI_VERIFY_NODE_ID_GLOBAL,
                self.peer_node.alias,
                self.node.node_id.to_bytes(),
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(verified_frames), 1)

        # 3. Global Verify Node ID with non-matching NodeID -> silent
        verified_frames.clear()
        await self.t2.send_frame(
            CanFrame.make_global_message(
                MTI_VERIFY_NODE_ID_GLOBAL,
                self.peer_node.alias,
                b"\x99\x99\x99\x99\x99\x99",
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(verified_frames), 0)

        # 4. Addressed Verify Node ID to node's alias (empty payload) -> replies
        await self.t2.send_frame(
            CanFrame.make_addressed_message(
                MTI_VERIFY_NODE_ID_ADDRESSED,
                self.peer_node.alias,
                self.node.alias,
                b"",
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(verified_frames), 1)

        # 5. Addressed Verify Node ID with matching NodeID -> replies
        verified_frames.clear()
        await self.t2.send_frame(
            CanFrame.make_addressed_message(
                MTI_VERIFY_NODE_ID_ADDRESSED,
                self.peer_node.alias,
                self.node.alias,
                self.node.node_id.to_bytes(),
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(verified_frames), 1)

    async def test_optional_interaction_rejected(self):
        """Unknown addressed MTI to node alias responds with OIR (0x0068) carrying error+MTI."""
        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        oir_frames = []
        self.t2.add_listener(
            lambda f: oir_frames.append(f) if f.mti == MTI_OPTIONAL_INTERACTION_REJECTED else None
        )

        # Unknown addressed MTI 0x0CC8 (has addressed bit 0x008 set)
        unknown_mti = 0x0CC8
        await self.t2.send_frame(
            CanFrame.make_addressed_message(unknown_mti, self.peer_node.alias, self.node.alias, b"")
        )
        await asyncio.sleep(0.03)

        self.assertEqual(len(oir_frames), 1)
        self.assertEqual(oir_frames[0].source_alias, self.node.alias)
        self.assertEqual(oir_frames[0].destination_alias, self.peer_node.alias)
        self.assertEqual(oir_frames[0].destination_payload, b"\x10\x00\x0C\xC8")

        # Known addressed MTI (Identify Events Addressed with no events registered) should NOT send OIR
        oir_frames.clear()
        await self.t2.send_frame(
            CanFrame.make_addressed_message(
                MTI_IDENTIFY_EVENTS_ADDRESSED,
                self.peer_node.alias,
                self.node.alias,
                b"",
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(oir_frames), 0)

    async def test_live_cdi_event_rebinding_via_memory_config(self):
        """Writing EventIDs and user_name into Space 0xFD dynamically rebinds components."""
        turnout = LCCTurnout(
            name="Yard Switch",
            close_event="05.01.01.01.14.01.00.01",
            throw_event="05.01.01.01.14.01.00.02",
            is_closed_event="05.01.01.01.14.01.00.03",
            is_thrown_event="05.01.01.01.14.01.00.04",
            initial_state=LCCTurnout.STATE_CLOSED,
        )
        sensor = LCCSensor(
            name="Approach Block",
            active_event="05.01.01.01.14.01.00.10",
            inactive_event="05.01.01.01.14.01.00.11",
        )
        signal = LCCSignal(
            name="Mast 1E",
            aspect_events={
                "STOP": "05.01.01.01.14.01.00.20",
                "CLEAR": "05.01.01.01.14.01.00.21",
            },
            initial_aspect="STOP",
        )
        output = LCCOutput(
            name="Depot Lamp",
            on_event="05.01.01.01.14.01.00.30",
            off_event="05.01.01.01.14.01.00.31",
            initial_state=LCCOutput.STATE_OFF,
        )

        for comp in (turnout, sensor, signal, output):
            self.node.add_component(comp)

        await self.node.start(check_delay_ms=10)
        await self.peer_node.start(check_delay_ms=10)

        cdi = self.node.cdi_doc
        new_throw = EventID("09.00.99.01.00.00.00.02")
        new_is_thrown = EventID("09.00.99.01.00.00.00.04")
        new_sensor_act = EventID("09.00.99.01.00.00.00.10")
        new_sig_clear = EventID("09.00.99.01.00.00.00.21")
        new_out_on = EventID("09.00.99.01.00.00.00.30")

        async def write_config(offset: int, payload: bytes):
            cmd = bytearray([0x20, 0x03])
            cmd.extend(offset.to_bytes(4, "big"))
            cmd.extend(payload)
            ok = await self.peer_node.send_datagram(self.node.alias, bytes(cmd))
            self.assertTrue(ok)

        # Write node_desc and new component EventIDs via Memory Configuration Protocol (0x20 / 0x03)
        await write_config(cdi.get_offset("node_desc"), b"East Yard Panel\x00\x00\x00\x00\x00")
        await write_config(cdi.get_offset("comp_1_throw"), new_throw.to_bytes())
        await write_config(cdi.get_offset("comp_1_is_thrown"), new_is_thrown.to_bytes())
        await write_config(cdi.get_offset("comp_2_act"), new_sensor_act.to_bytes())
        await write_config(cdi.get_offset("comp_3_clear"), new_sig_clear.to_bytes())
        await write_config(cdi.get_offset("comp_4_on"), new_out_on.to_bytes())

        await asyncio.sleep(0.02)

        # SNIP user_name updated live
        self.assertEqual(self.node.snip_data.user_name, "East Yard Panel")

        # Old events unregistered cleanly from EventManager
        self.assertFalse(self.node.event_manager.is_consumer("05.01.01.01.14.01.00.02"))
        self.assertFalse(self.node.event_manager.is_producer("05.01.01.01.14.01.00.10"))
        self.assertFalse(self.node.event_manager.is_consumer("05.01.01.01.14.01.00.21"))
        self.assertFalse(self.node.event_manager.is_consumer("05.01.01.01.14.01.00.30"))

        # New events are active in EventManager and on components
        self.assertEqual(turnout.throw_event, new_throw)
        self.assertEqual(sensor.active_event, new_sensor_act)
        self.assertEqual(signal.aspect_events["CLEAR"], new_sig_clear)
        self.assertEqual(output.on_event, new_out_on)
        self.assertTrue(self.node.event_manager.is_producer(new_sensor_act))

        # Old throw event no longer triggers turnout
        await self.peer_node.produce_event("05.01.01.01.14.01.00.02")
        await asyncio.sleep(0.03)
        self.assertEqual(turnout.state, LCCTurnout.STATE_CLOSED)

        # New throw event triggers turnout and emits new feedback event
        feedback = []
        self.peer_node.register_consumer(new_is_thrown, lambda eid: feedback.append(eid))
        await self.peer_node.produce_event(new_throw)
        await self.peer_node.produce_event(new_sig_clear)
        await self.peer_node.produce_event(new_out_on)
        await asyncio.sleep(0.05)

        self.assertEqual(turnout.state, LCCTurnout.STATE_THROWN)
        self.assertEqual(feedback, [new_is_thrown])
        self.assertEqual(signal.current_aspect, "CLEAR")
        self.assertTrue(output.is_on)

    async def test_persisted_cdi_config_rebinds_components_on_startup(self):
        """Saved config.bin values override defaults and rebind events on node start."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = os.path.join(tmpdir, "node_config.bin")
            new_throw = EventID("09.00.88.01.00.00.00.02")

            # Boot 1: write new EventID into persisted config space
            bus1 = LoopbackBus()
            t1 = LoopbackTransport(bus1)
            n1 = LCCNode("05.01.01.01.14.09", t1, config_file=cfg_path)
            turnout1 = LCCTurnout(
                name="Siding",
                close_event="05.01.01.01.14.09.00.01",
                throw_event="05.01.01.01.14.09.00.02",
            )
            n1.add_component(turnout1)
            await n1.start(check_delay_ms=10)
            n1.config_space.set_event_id(n1.cdi_doc.get_offset("comp_1_throw"), new_throw)
            await n1.stop()

            # Boot 2: fresh node with default initial EventID loads saved config_file
            bus2 = LoopbackBus()
            t2 = LoopbackTransport(bus2)
            t_peer = LoopbackTransport(bus2)
            n2 = LCCNode("05.01.01.01.14.09", t2, config_file=cfg_path)
            turnout2 = LCCTurnout(
                name="Siding",
                close_event="05.01.01.01.14.09.00.01",
                throw_event="05.01.01.01.14.09.00.02",
            )
            n2.add_component(turnout2)
            peer2 = LCCNode("02.01.12.00.00.09", t_peer)

            await n2.start(check_delay_ms=10)
            await peer2.start(check_delay_ms=10)

            self.assertEqual(turnout2.throw_event, new_throw)
            self.assertFalse(n2.event_manager.is_consumer("05.01.01.01.14.09.00.02"))
            self.assertTrue(n2.event_manager.is_consumer(new_throw))

            await peer2.produce_event(new_throw)
            await asyncio.sleep(0.03)
            self.assertEqual(turnout2.state, LCCTurnout.STATE_THROWN)

            await n2.stop()
            await peer2.stop()


if __name__ == "__main__":
    unittest.main()
