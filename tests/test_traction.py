"""
Unit tests for OpenLCB Traction Control Protocol (NMRA S-9.7.4.4).
Tests float16 speed/direction codec, discovery, throttle-to-train commands,
multi-frame replies, reservations, consist listeners, and LCCTrain component.
"""

import asyncio
import math
import unittest

from pylcc import (
    LCCNode,
    LCCTrain,
    NodeID,
    EventID,
    PIP_TRACTION_CONTROL,
    EVENT_IS_TRAIN,
    MTI_TRACTION_CONTROL_COMMAND,
    MTI_OPTIONAL_INTERACTION_REJECTED,
    MTI_PROTOCOL_SUPPORT_INQUIRY,
    MTI_PROTOCOL_SUPPORT_REPLY,
    MTI_IDENTIFY_PRODUCER,
    MTI_PRODUCER_IDENTIFIED_VALID,
    encode_float16,
    decode_float16,
)
from pylcc.can import CanFrame
from pylcc.traction import (
    LISTENER_FLAG_REVERSE,
    LISTENER_FLAG_LINK_F0,
    LISTENER_FLAG_LINK_FN,
)
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport


class TestFloat16Codec(unittest.TestCase):
    def test_signed_zero_and_nan(self):
        # Forward stop (+0.0)
        fwd_zero = encode_float16(0.0, reverse=False)
        self.assertEqual(fwd_zero, b"\x00\x00")
        spd, rev = decode_float16(fwd_zero)
        self.assertEqual(spd, 0.0)
        self.assertFalse(rev)

        # Reverse stop (-0.0)
        rev_zero = encode_float16(0.0, reverse=True)
        self.assertEqual(rev_zero, b"\x80\x00")
        spd, rev = decode_float16(rev_zero)
        self.assertEqual(spd, 0.0)
        self.assertTrue(rev)

        # NaN
        nan_bytes = encode_float16(float("nan"))
        self.assertEqual(nan_bytes, b"\x7f\xff")
        spd, rev = decode_float16(nan_bytes)
        self.assertTrue(math.isnan(spd))

    def test_speeds_and_directions(self):
        for target_spd, target_rev in (
            (1.0, False),
            (12.5, False),
            (25.0, True),
            (0.25, True),
            (100.0, False),
        ):
            encoded = encode_float16(target_spd, reverse=target_rev)
            decoded_spd, decoded_rev = decode_float16(encoded)
            self.assertAlmostEqual(decoded_spd, target_spd, places=2)
            self.assertEqual(decoded_rev, target_rev)

        # Negative float passed without explicit reverse flag sets reverse=True
        enc_neg = encode_float16(-15.5)
        dec_spd, dec_rev = decode_float16(enc_neg)
        self.assertAlmostEqual(dec_spd, 15.5, places=2)
        self.assertTrue(dec_rev)


class TestTractionControlProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bus = LoopbackBus()
        self.t_train = LoopbackTransport(self.bus)
        self.t_throttle = LoopbackTransport(self.bus)
        self.t_other = LoopbackTransport(self.bus)

        self.train_node = LCCNode("05.01.01.01.14.50", self.t_train)
        self.throttle_node = LCCNode("02.01.12.00.00.51", self.t_throttle)
        self.other_node = LCCNode("02.01.12.00.00.52", self.t_other)

    async def asyncTearDown(self):
        await self.train_node.stop()
        await self.throttle_node.stop()
        await self.other_node.stop()

    async def test_train_discovery_and_non_train_rejection(self):
        """Train node advertises PIP_TRACTION_CONTROL & EVENT_IS_TRAIN; non-train rejects 0x05EB."""
        self.train_node.enable_traction()
        await self.train_node.start(check_delay_ms=10)
        await self.throttle_node.start(check_delay_ms=10)
        await self.other_node.start(check_delay_ms=10)

        # 1. Check PIP flag on train node
        pip_replies = []
        self.t_throttle.add_listener(
            lambda f: pip_replies.append(f) if f.mti == MTI_PROTOCOL_SUPPORT_REPLY else None
        )
        await self.t_throttle.send_frame(
            CanFrame.make_addressed_message(
                MTI_PROTOCOL_SUPPORT_INQUIRY,
                self.throttle_node.alias,
                self.train_node.alias,
                b"",
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(pip_replies), 1)
        pip_val = int.from_bytes(pip_replies[0].destination_payload, "big")
        self.assertTrue(pip_val & PIP_TRACTION_CONTROL)

        # 2. Check Well-Known EVENT_IS_TRAIN Producer Identified reply
        prod_replies = []
        self.t_throttle.add_listener(
            lambda f: prod_replies.append(f) if f.mti == MTI_PRODUCER_IDENTIFIED_VALID else None
        )
        await self.t_throttle.send_frame(
            CanFrame.make_global_message(
                MTI_IDENTIFY_PRODUCER,
                self.throttle_node.alias,
                EventID(EVENT_IS_TRAIN).to_bytes(),
            )
        )
        await asyncio.sleep(0.03)
        self.assertEqual(len(prod_replies), 1)
        self.assertEqual(prod_replies[0].source_alias, self.train_node.alias)
        self.assertEqual(prod_replies[0].data, EventID(EVENT_IS_TRAIN).to_bytes())

        # 3. Non-train node (other_node) rejects MTI_TRACTION_CONTROL_COMMAND with OIR (0x0068)
        oir_frames = []
        self.t_throttle.add_listener(
            lambda f: oir_frames.append(f) if f.mti == MTI_OPTIONAL_INTERACTION_REJECTED else None
        )
        await self.throttle_node.traction_service.send_set_speed(self.other_node.alias, 10.0)
        await asyncio.sleep(0.03)
        self.assertEqual(len(oir_frames), 1)
        self.assertEqual(oir_frames[0].source_alias, self.other_node.alias)
        self.assertEqual(oir_frames[0].destination_payload, b"\x10\x00\x05\xeb")

    async def test_throttle_session_speed_functions_and_estop(self):
        """Test reserve, assign controller, set/query speed, functions, E-Stop, and release."""
        self.train_node.enable_traction()
        await self.train_node.start(check_delay_ms=10)
        await self.throttle_node.start(check_delay_ms=10)
        await self.other_node.start(check_delay_ms=10)

        throttle = self.throttle_node.traction_service
        other_throttle = self.other_node.traction_service
        train_alias = self.train_node.alias

        # Reserve session lock
        self.assertTrue(await throttle.reserve_train(train_alias))
        self.assertFalse(await other_throttle.reserve_train(train_alias))

        # Assign controller & verify via multi-frame Query Controller
        self.assertTrue(await throttle.assign_controller(train_alias))
        ctrl = await throttle.query_controller(train_alias)
        self.assertEqual(ctrl, self.throttle_node.node_id)

        # Command speed Forward 18.5 m/s and verify via multi-frame Query Speed
        await throttle.send_set_speed(train_alias, 18.5, reverse=False)
        await asyncio.sleep(0.02)
        spd_info = await throttle.query_speed(train_alias)
        self.assertIsNotNone(spd_info)
        self.assertAlmostEqual(spd_info["set_speed"], 18.5, places=1)
        self.assertFalse(spd_info["is_reverse"])
        self.assertFalse(spd_info["emergency_stop"])

        # Command Reverse Stop (-0.0) -> reverse=True preserved at speed 0
        await throttle.send_set_speed(train_alias, 0.0, reverse=True)
        await asyncio.sleep(0.02)
        spd_info = await throttle.query_speed(train_alias)
        self.assertEqual(spd_info["set_speed"], 0.0)
        self.assertTrue(spd_info["is_reverse"])

        # Command Reverse 12.0 m/s
        await throttle.send_set_speed(train_alias, 12.0, reverse=True)
        await asyncio.sleep(0.02)
        spd_info = await throttle.query_speed(train_alias)
        self.assertAlmostEqual(spd_info["set_speed"], 12.0, places=1)
        self.assertTrue(spd_info["is_reverse"])

        # Set & Query Functions (F0 headlight, F28 high function number)
        await throttle.send_set_function(train_alias, 0, 1)
        await throttle.send_set_function(train_alias, 28, 1)
        await asyncio.sleep(0.02)
        self.assertEqual(await throttle.query_function(train_alias, 0), 1)
        self.assertEqual(await throttle.query_function(train_alias, 28), 1)
        self.assertEqual(await throttle.query_function(train_alias, 5), 0)

        # Emergency Stop
        await throttle.send_emergency_stop(train_alias)
        await asyncio.sleep(0.02)
        spd_info = await throttle.query_speed(train_alias)
        self.assertEqual(spd_info["set_speed"], 0.0)
        self.assertTrue(spd_info["emergency_stop"])

        # Release controller and reservation
        await throttle.release_controller(train_alias)
        await throttle.release_train(train_alias)
        await asyncio.sleep(0.02)
        self.assertIsNone(await throttle.query_controller(train_alias))
        self.assertTrue(await other_throttle.reserve_train(train_alias))

    async def test_consist_listeners_and_forwarding(self):
        """Test consist attach/query/detach and command forwarding with reverse/F0 flags."""
        self.train_node.enable_traction()
        # other_node acts as a consist slave locomotive running reversed with F0 linked
        self.other_node.enable_traction()

        await self.train_node.start(check_delay_ms=10)
        await self.throttle_node.start(check_delay_ms=10)
        await self.other_node.start(check_delay_ms=10)

        # Cache slave AMD alias (happens automatically once enabled before slave start,
        # or explicitly via register_node_alias if leader started before slave)
        self.train_node.traction_service.register_node_alias(
            self.other_node.node_id,
            self.other_node.alias,
        )

        throttle = self.throttle_node.traction_service
        leader_alias = self.train_node.alias

        # Attach slave node with REVERSE + LINK_F0 flags
        ok = await throttle.attach_listener(
            leader_alias,
            self.other_node.node_id,
            flags=LISTENER_FLAG_REVERSE | LISTENER_FLAG_LINK_F0,
        )
        self.assertTrue(ok)

        count = await throttle.query_listeners(leader_alias)
        self.assertEqual(count, 1)

        entry = await throttle.query_listeners(leader_alias, index=0)
        self.assertEqual(entry["node_id"], self.other_node.node_id)
        self.assertEqual(entry["flags"], LISTENER_FLAG_REVERSE | LISTENER_FLAG_LINK_F0)

        # Drive leader Forward 20.0 m/s -> slave must receive Reverse 20.0 m/s
        await throttle.send_set_speed(leader_alias, 20.0, reverse=False)
        await throttle.send_set_function(leader_alias, 0, 1)  # F0 linked
        await throttle.send_set_function(leader_alias, 2, 1)  # F2 not linked (LINK_FN not set)
        await asyncio.sleep(0.04)

        slave_svc = self.other_node.traction_service
        self.assertAlmostEqual(slave_svc.set_speed, 20.0, places=1)
        self.assertTrue(slave_svc.is_reverse)
        self.assertEqual(slave_svc.functions.get(0), 1)
        self.assertNotIn(2, slave_svc.functions)

        # E-Stop forwards to consist listener
        await throttle.send_emergency_stop(leader_alias)
        await asyncio.sleep(0.03)
        self.assertTrue(slave_svc.emergency_stop)
        self.assertEqual(slave_svc.set_speed, 0.0)

        # Detach listener
        self.assertTrue(await throttle.detach_listener(leader_alias, self.other_node.node_id))
        self.assertEqual(await throttle.query_listeners(leader_alias), 0)

    async def test_lcc_train_component_callbacks(self):
        """Test high-level LCCTrain component hooks and convenience properties."""
        events = []
        train = LCCTrain(
            name="SP 4449 Daylight",
            initial_functions={0: 1},
            on_speed_change=lambda spd, rev: events.append(("speed", round(spd, 2), rev)),
            on_function_change=lambda fn, val: events.append(("fn", fn, val)),
            on_emergency_stop=lambda: events.append(("estop",)),
        )
        self.train_node.add_component(train)

        await self.train_node.start(check_delay_ms=10)
        await self.throttle_node.start(check_delay_ms=10)

        self.assertEqual(train.get_function(0), 1)

        throttle = self.throttle_node.traction_service
        await throttle.send_set_speed(self.train_node.alias, 14.0, reverse=False)
        await throttle.send_set_function(self.train_node.alias, 1, 1)
        await throttle.send_emergency_stop(self.train_node.alias)
        await asyncio.sleep(0.04)

        self.assertEqual(events, [
            ("speed", 14.0, False),
            ("fn", 1, 1),
            ("estop",),
        ])
        self.assertTrue(train.emergency_stop)
        self.assertEqual(train.speed, 0.0)
        self.assertEqual(train.get_function(1), 1)


if __name__ == "__main__":
    unittest.main()
