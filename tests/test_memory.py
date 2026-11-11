"""
Unit tests for OpenLCB Memory Configuration Protocol and CDI XML Generator.
NMRA Standards S-9.7.4.2 & S-9.7.4.1.
"""

import unittest
import asyncio
import os
import tempfile
import struct

from pylcc.node_id import NodeID
from pylcc.event_id import EventID
from pylcc.alias import AliasManager, STATE_PERMITTED
from pylcc.datagram import DatagramManager
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.memory import (
    MemorySpace,
    BytesMemorySpace,
    ConfigMemorySpace,
    MemoryConfigurationService,
    DATAGRAM_PROTOCOL_MEMORY_CONFIG,
    SPACE_CDI,
    SPACE_ALL_MEMORY,
    SPACE_CONFIG,
    CMD_READ_GENERIC,
    CMD_READ_CDI,
    CMD_READ_CONFIG,
    CMD_READ_REPLY_GENERIC,
    CMD_READ_REPLY_CDI,
    CMD_READ_REPLY_CONFIG,
    CMD_READ_REPLY_FAILURE,
    CMD_WRITE_GENERIC,
    CMD_WRITE_CONFIG,
    CMD_WRITE_REPLY_GENERIC,
    CMD_WRITE_REPLY_CONFIG,
    CMD_WRITE_REPLY_FAILURE,
    CMD_GET_CONFIG_OPTIONS,
    CMD_CONFIG_OPTIONS_REPLY,
    CMD_GET_SPACE_INFO,
    CMD_SPACE_INFO_REPLY,
    CMD_RESTART,
)
from pylcc.cdi import (
    CdiDocument,
    CdiSegment,
    CdiGroup,
    CdiEventID,
    CdiInt,
    CdiString,
)


class TestMemorySpaces(unittest.TestCase):
    """Test BytesMemorySpace and ConfigMemorySpace storage backends."""

    def test_bytes_memory_space(self):
        space = BytesMemorySpace(SPACE_CDI, b"HelloWorld", read_only=True)
        self.assertEqual(space.get_size(), 10)
        self.assertEqual(space.read(0, 5), b"Hello")
        self.assertEqual(space.read(5, 5), b"World")
        # Reading beyond size returns null byte
        self.assertEqual(space.read(10, 5), b"\x00")
        # Writing to read-only space returns False
        self.assertFalse(space.write(0, b"Fail"))

        # Writable BytesMemorySpace
        rw_space = BytesMemorySpace(SPACE_CONFIG, b"Test", read_only=False)
        self.assertTrue(rw_space.write(0, b"Best"))
        self.assertEqual(rw_space.read(0, 4), b"Best")
        # Expanding write
        self.assertTrue(rw_space.write(6, b"Data"))
        self.assertEqual(rw_space.read(0, 10), b"Best\x00\x00Data")

    def test_config_memory_space_persistence(self):
        with tempfile.NamedTemporaryFile(delete=False) as tf:
            temp_path = tf.name

        try:
            writes = []
            def on_write(addr, data):
                writes.append((addr, data))

            cfg = ConfigMemorySpace(size=64, on_write=on_write, filename=temp_path)
            self.assertEqual(cfg.get_size(), 64)

            # Write data
            self.assertTrue(cfg.write(0, b"\x01\x02\x03\x04"))
            self.assertEqual(len(writes), 1)
            self.assertEqual(writes[0], (0, b"\x01\x02\x03\x04"))

            # Load into a second instance from the same file
            cfg2 = ConfigMemorySpace(size=64, filename=temp_path)
            self.assertEqual(cfg2.read(0, 4), b"\x01\x02\x03\x04")
            self.assertEqual(cfg2.read(4, 4), b"\x00\x00\x00\x00")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_config_memory_typed_accessors(self):
        cfg = ConfigMemorySpace(size=128)

        # Byte
        cfg.set_byte(0, 0xAB)
        self.assertEqual(cfg.get_byte(0), 0xAB)

        # UInt16
        cfg.set_uint16(2, 0x1234)
        self.assertEqual(cfg.get_uint16(2), 0x1234)

        # UInt32
        cfg.set_uint32(4, 0xDEADBEEF)
        self.assertEqual(cfg.get_uint32(4), 0xDEADBEEF)

        # EventID
        test_eid = EventID("05.01.01.01.14.01.00.02")
        cfg.set_event_id(8, test_eid)
        self.assertEqual(cfg.get_event_id(8), test_eid)

        # String
        cfg.set_string(16, "Main Line Turnout", 20)
        self.assertEqual(cfg.get_string(16, 20), "Main Line Turnout")


class TestCdiGenerator(unittest.TestCase):
    """Test programmatic CDI generation and XML validation."""

    def test_cdi_document_xml_and_offsets(self):
        doc = CdiDocument(
            manufacturer="SVL Model Railroad",
            model="pyLCC-8IO",
            hardware_version="Rev B",
            software_version="1.0.0",
        )

        seg = doc.add_segment(space=SPACE_CONFIG, origin=0, name="Configuration", description="Node settings")
        seg.add_string("node_name", name="Node Name", description="User node name", size=16, default="Signal Node")
        seg.add_int("debounce_ms", name="Debounce", size=2, min_val=10, max_val=500, default=50)

        grp = seg.add_group("turnouts", name="Turnout Settings", replication=2, repname="Turnout")
        grp.add_eventid("evt_closed", name="Closed Event", default="05.01.01.01.14.01.00.01")
        grp.add_eventid("evt_thrown", name="Thrown Event", default="05.01.01.01.14.01.00.02")
        grp.add_int("invert", name="Invert", size=1, default=0, map_values={0: "Normal", 1: "Inverted"})

        doc.build()

        # Check calculated offsets
        self.assertEqual(doc.get_offset("node_name"), 0)
        self.assertEqual(doc.get_offset("debounce_ms"), 16)
        # grp starts at 16 + 2 = 18
        # Each turnout replica: evt_closed(8) + evt_thrown(8) + invert(1) = 17 bytes
        # 2 replicas = 34 bytes
        # Total segment size: 18 + 34 = 52 bytes
        self.assertEqual(seg.get_size(), 52)

        # Generate XML
        xml = doc.to_xml()
        self.assertIn("<cdi", xml)
        self.assertIn("<manufacturer>SVL Model Railroad</manufacturer>", xml)
        self.assertIn("<model>pyLCC-8IO</model>", xml)
        self.assertIn("<hardwareVersion>Rev B</hardwareVersion>", xml)
        self.assertIn("<softwareVersion>1.0.0</softwareVersion>", xml)
        self.assertIn('<segment space="253">', xml)
        self.assertIn('<string size="16">', xml)
        self.assertIn('<int size="2">', xml)
        self.assertIn('<group replication="2">', xml)
        self.assertIn("<repname>Turnout</repname>", xml)
        self.assertIn("<eventid>", xml)
        self.assertIn("<relation>", xml)
        self.assertIn("<property>1</property>", xml)
        self.assertIn("<value>Inverted</value>", xml)

        # Null-terminated bytes
        cdi_bytes = doc.to_bytes()
        self.assertTrue(cdi_bytes.endswith(b"\x00"))

        # Create spaces
        cdi_space = doc.create_cdi_space()
        self.assertEqual(cdi_space.space_id, SPACE_CDI)
        self.assertEqual(cdi_space.get_size(), len(cdi_bytes))

        config_space = doc.create_config_space(min_size=128)
        self.assertEqual(config_space.space_id, SPACE_CONFIG)
        self.assertGreaterEqual(config_space.get_size(), 128)

        # Verify default configuration values in config_space
        self.assertEqual(config_space.get_string(0, 16), "Signal Node")
        self.assertEqual(config_space.get_uint16(16), 50)


class TestMemoryConfigurationProtocol(unittest.IsolatedAsyncioTestCase):
    """Test full OpenLCB Memory Configuration Datagram Protocol (0x20)."""

    async def asyncSetUp(self):
        self.bus = LoopbackBus()
        self.transport1 = LoopbackTransport(self.bus)
        self.transport2 = LoopbackTransport(self.bus)

        # Node 1: Client / Configurator (e.g. JMRI)
        self.alias_mgr1 = AliasManager("02.01.12.00.00.01", self.transport1)
        self.alias_mgr1.alias = 0x301
        self.alias_mgr1.state = STATE_PERMITTED
        self.datagram_mgr1 = DatagramManager("02.01.12.00.00.01", self.alias_mgr1, self.transport1)

        # Node 2: Managed Node (Target)
        self.alias_mgr2 = AliasManager("05.01.01.01.14.01", self.transport2)
        self.alias_mgr2.alias = 0x302
        self.alias_mgr2.state = STATE_PERMITTED
        self.datagram_mgr2 = DatagramManager("05.01.01.01.14.01", self.alias_mgr2, self.transport2)

        # Wire incoming frames to datagram managers
        self.transport1.add_listener(lambda f: asyncio.create_task(self.datagram_mgr1.process_frame(f)))
        self.transport2.add_listener(lambda f: asyncio.create_task(self.datagram_mgr2.process_frame(f)))

        self.mem_service = MemoryConfigurationService(
            node_id="05.01.01.01.14.01",
            alias_manager=self.alias_mgr2,
            datagram_manager=self.datagram_mgr2,
        )

        # Build CDI and spaces for target node
        self.cdi_doc = CdiDocument(manufacturer="SVL", model="TestNode")
        seg = self.cdi_doc.add_segment(space=SPACE_CONFIG, origin=0, name="Config")
        seg.add_string("node_desc", name="Desc", size=16, default="InitialDesc")
        seg.add_eventid("event_1", name="Evt", default="05.01.01.01.14.01.00.01")
        self.cdi_doc.build()

        self.cdi_space = self.cdi_doc.create_cdi_space()
        self.config_space = self.cdi_doc.create_config_space(min_size=64)
        self.mem_service.register_space(self.cdi_space)
        self.mem_service.register_space(self.config_space)

    async def asyncTearDown(self):
        await self.transport1.close()
        await self.transport2.close()

    async def test_read_cdi_space_0xFF(self):
        """Test reading CDI XML via command 0x41."""
        received_replies = []
        fut = asyncio.get_event_loop().create_future()

        async def cdi_reply_handler(datagram):
            if datagram.data[0] == DATAGRAM_PROTOCOL_MEMORY_CONFIG and datagram.data[1] == CMD_READ_REPLY_CDI:
                received_replies.append(datagram.data)
                if not fut.done():
                    fut.set_result(datagram.data)
            return True

        self.datagram_mgr1.register_handler(DATAGRAM_PROTOCOL_MEMORY_CONFIG, cdi_reply_handler)

        # Read 32 bytes from offset 0
        req = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_READ_CDI])
        req.extend(struct.pack(">I", 0))  # Address 0
        req.append(32)                    # Count 32

        ok = await self.datagram_mgr1.send_datagram(0x302, bytes(req))
        self.assertTrue(ok)

        reply_data = await asyncio.wait_for(fut, timeout=1.0)
        # Reply format: [0x20, 0x51, Addr(4), Data(32)]
        self.assertEqual(reply_data[0], DATAGRAM_PROTOCOL_MEMORY_CONFIG)
        self.assertEqual(reply_data[1], CMD_READ_REPLY_CDI)
        addr = struct.unpack(">I", reply_data[2:6])[0]
        self.assertEqual(addr, 0)
        xml_chunk = reply_data[6:].decode("utf-8")
        self.assertTrue(xml_chunk.startswith("<?xml"))

    async def test_read_and_write_config_space_0xFD(self):
        """Test reading and writing configuration memory via commands 0x43 and 0x03."""
        received_replies = []
        fut = asyncio.get_event_loop().create_future()

        async def mem_reply_handler(datagram):
            if datagram.data[0] == DATAGRAM_PROTOCOL_MEMORY_CONFIG:
                received_replies.append(datagram.data)
                if not fut.done():
                    fut.set_result(datagram.data)
            return True

        self.datagram_mgr1.register_handler(DATAGRAM_PROTOCOL_MEMORY_CONFIG, mem_reply_handler)

        # 1. Write "NewDescription\x00" to address 0 in space 0xFD
        write_req = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_WRITE_CONFIG])
        write_req.extend(struct.pack(">I", 0))  # Address 0
        write_req.extend(b"NewDescription\x00")

        fut = asyncio.get_event_loop().create_future()
        ok = await self.datagram_mgr1.send_datagram(0x302, bytes(write_req))
        self.assertTrue(ok)

        write_reply = await asyncio.wait_for(fut, timeout=1.0)
        self.assertEqual(write_reply[0], DATAGRAM_PROTOCOL_MEMORY_CONFIG)
        self.assertEqual(write_reply[1], CMD_WRITE_REPLY_CONFIG)
        self.assertEqual(struct.unpack(">I", write_reply[2:6])[0], 0)

        # Verify internal config space was updated
        self.assertEqual(self.config_space.get_string(0, 16), "NewDescription")

        # 2. Read back from address 0
        read_req = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_READ_CONFIG])
        read_req.extend(struct.pack(">I", 0))
        read_req.append(15)

        fut = asyncio.get_event_loop().create_future()
        ok = await self.datagram_mgr1.send_datagram(0x302, bytes(read_req))
        self.assertTrue(ok)

        read_reply = await asyncio.wait_for(fut, timeout=1.0)
        self.assertEqual(read_reply[0], DATAGRAM_PROTOCOL_MEMORY_CONFIG)
        self.assertEqual(read_reply[1], CMD_READ_REPLY_CONFIG)
        self.assertEqual(read_reply[6:], b"NewDescription\x00")

    async def test_get_configuration_options(self):
        """Test Get Configuration Options (0x80) and 0x82 Reply."""
        fut = asyncio.get_event_loop().create_future()

        async def reply_handler(datagram):
            if datagram.data[0] == DATAGRAM_PROTOCOL_MEMORY_CONFIG and datagram.data[1] == CMD_CONFIG_OPTIONS_REPLY:
                fut.set_result(datagram.data)
            return True

        self.datagram_mgr1.register_handler(DATAGRAM_PROTOCOL_MEMORY_CONFIG, reply_handler)

        req = bytes([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_GET_CONFIG_OPTIONS])
        ok = await self.datagram_mgr1.send_datagram(0x302, req)
        self.assertTrue(ok)

        reply = await asyncio.wait_for(fut, timeout=1.0)
        # Reply: [0x20, 0x82, Options(2), WriteLengths(1), HighSpace(1), LowSpace(1)]
        self.assertEqual(reply[0], DATAGRAM_PROTOCOL_MEMORY_CONFIG)
        self.assertEqual(reply[1], CMD_CONFIG_OPTIONS_REPLY)
        options = struct.unpack(">H", reply[2:4])[0]
        # Check space 0xFD and 0xFF supported flags
        self.assertTrue(bool(options & 0x1000))  # Space 0xFD
        self.assertTrue(bool(options & 0x0400))  # Space 0xFF
        self.assertEqual(reply[5], SPACE_CDI)     # Highest space
        self.assertEqual(reply[6], SPACE_CONFIG)  # Lowest space

    async def test_get_space_info(self):
        """Test Get Space Info (0x84) and 0x86 Reply."""
        fut = asyncio.get_event_loop().create_future()

        async def reply_handler(datagram):
            if datagram.data[0] == DATAGRAM_PROTOCOL_MEMORY_CONFIG and datagram.data[1] == CMD_SPACE_INFO_REPLY:
                fut.set_result(datagram.data)
            return True

        self.datagram_mgr1.register_handler(DATAGRAM_PROTOCOL_MEMORY_CONFIG, reply_handler)

        # Query Space 0xFD info
        req = bytes([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_GET_SPACE_INFO, SPACE_CONFIG])
        ok = await self.datagram_mgr1.send_datagram(0x302, req)
        self.assertTrue(ok)

        reply = await asyncio.wait_for(fut, timeout=1.0)
        self.assertEqual(reply[0], DATAGRAM_PROTOCOL_MEMORY_CONFIG)
        self.assertEqual(reply[1], CMD_SPACE_INFO_REPLY)
        self.assertEqual(reply[2], SPACE_CONFIG)
        highest_addr = struct.unpack(">I", reply[3:7])[0]
        self.assertEqual(highest_addr, self.config_space.get_size() - 1)
        flags = reply[7]
        self.assertEqual(flags & 0x01, 0)  # Config space is writable (not read-only)

        # Query CDI Space 0xFF (Read-only)
        fut = asyncio.get_event_loop().create_future()
        req_cdi = bytes([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_GET_SPACE_INFO, SPACE_CDI])
        await self.datagram_mgr1.send_datagram(0x302, req_cdi)
        reply_cdi = await asyncio.wait_for(fut, timeout=1.0)
        self.assertEqual(reply_cdi[2], SPACE_CDI)
        self.assertEqual(reply_cdi[7] & 0x01, 1)  # CDI is read-only!

    async def test_write_to_read_only_space_fails(self):
        """Test that writing to read-only space returns Write Failure (0x18)."""
        fut = asyncio.get_event_loop().create_future()

        async def reply_handler(datagram):
            if datagram.data[0] == DATAGRAM_PROTOCOL_MEMORY_CONFIG and datagram.data[1] == CMD_WRITE_REPLY_FAILURE:
                fut.set_result(datagram.data)
            return True

        self.datagram_mgr1.register_handler(DATAGRAM_PROTOCOL_MEMORY_CONFIG, reply_handler)

        # Attempt to write to Space 0xFF (CDI) via generic write 0x00
        req = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_WRITE_GENERIC])
        req.extend(struct.pack(">I", 0))  # Address 0
        req.append(SPACE_CDI)             # Space CDI
        req.extend(b"IllegalWrite")

        await self.datagram_mgr1.send_datagram(0x302, bytes(req))
        reply = await asyncio.wait_for(fut, timeout=1.0)
        self.assertEqual(reply[0], DATAGRAM_PROTOCOL_MEMORY_CONFIG)
        self.assertEqual(reply[1], CMD_WRITE_REPLY_FAILURE)

    async def test_restart_command(self):
        """Test node restart command (0xA8)."""
        restarted = False
        def on_restart():
            nonlocal restarted
            restarted = True

        self.mem_service.set_on_restart(on_restart)

        req = bytes([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_RESTART])
        ok = await self.datagram_mgr1.send_datagram(0x302, req)
        self.assertTrue(ok)
        await asyncio.sleep(0.05)
        self.assertTrue(restarted)


if __name__ == "__main__":
    unittest.main()
