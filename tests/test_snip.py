import asyncio
import unittest
from pylcc.node_id import NodeID
from pylcc.alias import AliasManager
from pylcc.can import CanFrame
from pylcc.snip import (
    SnipData,
    SnipService,
    MTI_PROTOCOL_SUPPORT_INQUIRY,
    MTI_PROTOCOL_SUPPORT_REPLY,
    MTI_SIMPLE_NODE_IDENT_INFO_REQUEST,
    MTI_SIMPLE_NODE_IDENT_INFO_REPLY,
    PIP_SIMPLE_NODE,
    PIP_DATAGRAM,
    PIP_EVENT_EXCHANGE,
    PIP_SNIP,
    PIP_MEMORY_CONFIG,
    PIP_CDI,
)
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport


class TestSnip(unittest.IsolatedAsyncioTestCase):
    def test_snip_data_encode_decode(self):
        original = SnipData(
            manufacturer="OpenLCB Test Lab",
            model="pyLCC-Pico",
            hardware_version="Rev B",
            software_version="1.0.0",
            user_name="Tower Signal Controller",
            user_description="Controls 4 crossovers and 2 searchlight signals",
        )
        encoded = original.encode()
        decoded = SnipData.decode(encoded)

        self.assertEqual(decoded.manufacturer, original.manufacturer)
        self.assertEqual(decoded.model, original.model)
        self.assertEqual(decoded.hardware_version, original.hardware_version)
        self.assertEqual(decoded.software_version, original.software_version)
        self.assertEqual(decoded.user_name, original.user_name)
        self.assertEqual(decoded.user_description, original.user_description)

    async def test_protocol_support_inquiry(self):
        bus = LoopbackBus()
        t_node = LoopbackTransport(bus)
        t_tester = LoopbackTransport(bus)

        alias_node = AliasManager("05.01.01.01.14.01", t_node)
        await alias_node.allocate(check_delay_ms=10)

        snip_service = SnipService("05.01.01.01.14.01", alias_node, t_node)
        t_node.add_listener(lambda f: asyncio.create_task(snip_service.process_frame(f)))

        tester_rx = []
        t_tester.add_listener(lambda f: tester_rx.append(f))

        # Tester sends Protocol Support Inquiry (PIP)
        pip_inquiry = CanFrame.make_addressed_message(
            MTI_PROTOCOL_SUPPORT_INQUIRY,
            source_alias=0x999,
            dest_alias=alias_node.alias
        )
        await t_tester.send_frame(pip_inquiry)
        await asyncio.sleep(0.05)

        self.assertEqual(len(tester_rx), 1)
        resp = tester_rx[0]
        self.assertEqual(resp.mti, MTI_PROTOCOL_SUPPORT_REPLY)
        self.assertEqual(resp.source_alias, alias_node.alias)
        self.assertEqual(resp.destination_alias, 0x999)

        pip_mask = int.from_bytes(resp.destination_payload, "big")
        # Check that core protocols are declared supported
        self.assertTrue(bool(pip_mask & PIP_SIMPLE_NODE))
        self.assertTrue(bool(pip_mask & PIP_DATAGRAM))
        self.assertTrue(bool(pip_mask & PIP_EVENT_EXCHANGE))
        self.assertTrue(bool(pip_mask & PIP_SNIP))
        self.assertTrue(bool(pip_mask & PIP_MEMORY_CONFIG))
        self.assertTrue(bool(pip_mask & PIP_CDI))

        await t_node.close()
        await t_tester.close()

    async def test_snip_request_and_reply_stream(self):
        bus = LoopbackBus()
        t_node = LoopbackTransport(bus)
        t_tester = LoopbackTransport(bus)

        alias_node = AliasManager("05.01.01.01.14.01", t_node)
        await alias_node.allocate(check_delay_ms=10)

        custom_snip = SnipData(
            manufacturer="SVL Railroad",
            model="pyLCC-ESP32C6",
            hardware_version="v2.0",
            software_version="0.1.0",
            user_name="Main St Interlocking",
            user_description="Branchline Junction",
        )
        snip_service = SnipService("05.01.01.01.14.01", alias_node, t_node, snip_data=custom_snip)
        t_node.add_listener(lambda f: asyncio.create_task(snip_service.process_frame(f)))

        tester_rx = []
        t_tester.add_listener(lambda f: tester_rx.append(f))

        # Tester sends Simple Node Ident Info Request
        req_frame = CanFrame.make_addressed_message(
            MTI_SIMPLE_NODE_IDENT_INFO_REQUEST,
            source_alias=0x999,
            dest_alias=alias_node.alias
        )
        await t_tester.send_frame(req_frame)
        await asyncio.sleep(0.05)

        # Tester should receive multiple reply chunks
        self.assertGreater(len(tester_rx), 1)
        reassembled_bytes = bytearray()
        for f in tester_rx:
            self.assertEqual(f.mti, MTI_SIMPLE_NODE_IDENT_INFO_REPLY)
            self.assertEqual(f.destination_alias, 0x999)
            self.assertEqual(f.source_alias, alias_node.alias)
            reassembled_bytes.extend(f.destination_payload)

        # Decode received payload
        received_snip = SnipData.decode(bytes(reassembled_bytes))
        self.assertEqual(received_snip.manufacturer, "SVL Railroad")
        self.assertEqual(received_snip.model, "pyLCC-ESP32C6")
        self.assertEqual(received_snip.hardware_version, "v2.0")
        self.assertEqual(received_snip.software_version, "0.1.0")
        self.assertEqual(received_snip.user_name, "Main St Interlocking")
        self.assertEqual(received_snip.user_description, "Branchline Junction")

        await t_node.close()
        await t_tester.close()


if __name__ == "__main__":
    unittest.main()
