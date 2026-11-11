"""
Unit tests for pyLCC Unified Node Orchestrator and Layout Components.
"""

import unittest
import asyncio
from pylcc.node import Node
from pylcc.node_id import NodeID
from pylcc.event_id import EventID
from pylcc.can import CanFrame
from pylcc.events import MTI_PCER
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.components import (
    Turnout,
    Sensor,
    Signal,
    Output,
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


if __name__ == "__main__":
    unittest.main()
