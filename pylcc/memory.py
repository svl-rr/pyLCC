"""
OpenLCB Memory Configuration Protocol.
Compliant with NMRA Standard S-9.7.4.2.
Handles Memory Read, Memory Write, Get Options, and Get Space Info via Datagrams (Protocol 0x20).
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

import struct
try:
    from micropython import const
except ImportError:
    def const(x):
        return x

from .can import CanFrame
from .node_id import NodeID
from .event_id import EventID
from .datagram import Datagram, DatagramManager


# Datagram Protocol Identifier for Memory Configuration
DATAGRAM_PROTOCOL_MEMORY_CONFIG = const(0x20)

# Standard OpenLCB Memory Space Identifiers
SPACE_CDI         = const(0xFF)  # Configuration Description Information (XML, Read-Only)
SPACE_ALL_MEMORY  = const(0xFE)  # All Memory Space
SPACE_CONFIG      = const(0xFD)  # Non-volatile Configuration Memory (Read/Write)
SPACE_ACDI_MFG    = const(0xFC)  # ACDI Manufacturer Information (Read-Only)
SPACE_ACDI_USER   = const(0xFB)  # ACDI User Information (Read/Write)

# Memory Configuration Command Opcodes (Byte 1)
CMD_WRITE_GENERIC = const(0x00)  # Write with space byte
CMD_WRITE_CDI     = const(0x01)  # Write to Space 0xFF (CDI)
CMD_WRITE_ALL_MEM = const(0x02)  # Write to Space 0xFE
CMD_WRITE_CONFIG  = const(0x03)  # Write to Space 0xFD (Config Memory)

CMD_WRITE_REPLY_GENERIC = const(0x10)  # Write Reply with space byte
CMD_WRITE_REPLY_CONFIG  = const(0x13)  # Write Reply for Space 0xFD
CMD_WRITE_REPLY_FAILURE = const(0x18)  # Write Failure Reply

CMD_READ_GENERIC  = const(0x40)  # Read with space byte
CMD_READ_CDI      = const(0x41)  # Read from Space 0xFF (CDI)
CMD_READ_ALL_MEM  = const(0x42)  # Read from Space 0xFE
CMD_READ_CONFIG   = const(0x43)  # Read from Space 0xFD (Config Memory)

CMD_READ_REPLY_GENERIC = const(0x50)  # Read Reply with space byte
CMD_READ_REPLY_CDI     = const(0x51)  # Read Reply for Space 0xFF (CDI)
CMD_READ_REPLY_ALL_MEM = const(0x52)  # Read Reply for Space 0xFE
CMD_READ_REPLY_CONFIG  = const(0x53)  # Read Reply for Space 0xFD

CMD_READ_REPLY_FAILURE = const(0x58)  # Read Failure Reply

CMD_GET_CONFIG_OPTIONS   = const(0x80)  # Get Configuration Options Request
CMD_CONFIG_OPTIONS_REPLY = const(0x82)  # Configuration Options Reply
CMD_GET_SPACE_INFO       = const(0x84)  # Get Space Info Request
CMD_SPACE_INFO_REPLY     = const(0x86)  # Space Info Reply
CMD_RESTART              = const(0xA8)  # Reboot / Restart Node


class MemorySpace:
    """Base class for an addressable memory space on an OpenLCB node."""
    __slots__ = ("space_id", "read_only")

    def __init__(self, space_id: int, read_only: bool = False):
        self.space_id = space_id
        self.read_only = read_only

    def read(self, address: int, count: int) -> bytes:
        raise NotImplementedError

    def write(self, address: int, data: bytes) -> bool:
        if self.read_only:
            return False
        raise NotImplementedError

    def get_size(self) -> int:
        raise NotImplementedError


class BytesMemorySpace(MemorySpace):
    """Memory space backed by in-memory bytes or bytearray (e.g. CDI XML or static tables)."""
    __slots__ = ("_data",)

    def __init__(self, space_id: int, data: bytes = b"", read_only: bool = True):
        super().__init__(space_id, read_only=read_only)
        self._data = data if (read_only and isinstance(data, bytes)) else bytearray(data)

    def set_data(self, data: bytes):
        self._data = data if (self.read_only and isinstance(data, bytes)) else bytearray(data)

    def read(self, address: int, count: int) -> bytes:
        if address >= len(self._data):
            return b"\x00"  # End of data / null-terminator
        chunk = self._data[address:address + count]
        return chunk if isinstance(chunk, bytes) else bytes(chunk)

    def write(self, address: int, data: bytes) -> bool:
        if self.read_only:
            return False
        if isinstance(self._data, bytes):
            self._data = bytearray(self._data)
        # Expand buffer if needed
        required_len = address + len(data)
        if required_len > len(self._data):
            self._data.extend(b"\x00" * (required_len - len(self._data)))
        self._data[address:required_len] = data
        return True

    def get_size(self) -> int:
        return len(self._data)


class ConfigMemorySpace(MemorySpace):
    """
    Persistent Configuration Memory (Space 0xFD).
    Maintains user settings and event IDs, with optional persistence callback or file storage.
    """
    __slots__ = ("size", "on_write", "filename", "_data")
    def __init__(self, size: int = 512, on_write=None, filename: str = None, default_data: bytes = None):
        super().__init__(SPACE_CONFIG, read_only=False)
        self.size = size
        self.on_write = on_write
        self.filename = filename
        if default_data:
            self._data = bytearray(default_data)
            if len(self._data) < size:
                self._data.extend(b"\x00" * (size - len(self._data)))
            elif len(self._data) > size:
                self.size = len(self._data)
        else:
            self._data = bytearray(size)

        # Attempt to load from storage if filename provided
        if self.filename:
            self.load()

    def load(self):
        """Load configuration from flash filesystem if file exists."""
        try:
            with open(self.filename, "rb") as f:
                content = f.read()
                limit = min(len(content), self.size)
                self._data[:limit] = content[:limit]
        except (OSError, Exception):
            pass

    def save(self):
        """Persist configuration to flash filesystem."""
        if not self.filename:
            return
        try:
            with open(self.filename, "wb") as f:
                f.write(self._data)
        except (OSError, Exception):
            pass

    def read(self, address: int, count: int) -> bytes:
        if address >= self.size:
            return b""
        end = min(address + count, self.size)
        return bytes(self._data[address:end])

    def write(self, address: int, data: bytes) -> bool:
        if address >= self.size:
            return False
        limit = min(len(data), self.size - address)
        self._data[address:address + limit] = data[:limit]

        # Trigger on_write callback if registered
        if self.on_write:
            try:
                self.on_write(address, data[:limit])
            except Exception:
                pass

        if self.filename:
            self.save()
        return True

    def get_size(self) -> int:
        return self.size

    # --- Typed Field Accessors for Application Components ---

    def get_byte(self, offset: int) -> int:
        if 0 <= offset < self.size:
            return self._data[offset]
        return 0

    def set_byte(self, offset: int, value: int):
        self.write(offset, bytes([value & 0xFF]))

    def get_uint16(self, offset: int) -> int:
        if 0 <= offset + 1 < self.size:
            return struct.unpack(">H", self._data[offset:offset + 2])[0]
        return 0

    def set_uint16(self, offset: int, value: int):
        self.write(offset, struct.pack(">H", value & 0xFFFF))

    def get_uint32(self, offset: int) -> int:
        if 0 <= offset + 3 < self.size:
            return struct.unpack(">I", self._data[offset:offset + 4])[0]
        return 0

    def set_uint32(self, offset: int, value: int):
        self.write(offset, struct.pack(">I", value & 0xFFFFFFFF))

    def get_event_id(self, offset: int) -> EventID:
        if 0 <= offset + 7 < self.size:
            return EventID(self._data[offset:offset + 8])
        return EventID(0)

    def set_event_id(self, offset: int, event_id):
        eid = EventID(event_id)
        self.write(offset, eid.to_bytes())

    def get_string(self, offset: int, length: int) -> str:
        if 0 <= offset < self.size:
            raw = self._data[offset:min(offset + length, self.size)]
            null_pos = raw.find(0)
            if null_pos != -1:
                raw = raw[:null_pos]
            try:
                return raw.decode("utf-8")
            except Exception:
                return raw.decode("latin-1", "replace")
        return ""

    def set_string(self, offset: int, value: str, length: int):
        raw = value.encode("utf-8")[:length]
        padded = raw + b"\x00" * (length - len(raw))
        self.write(offset, padded)


def _decode_cstr(raw) -> str:
    """Decode a null-terminated UTF-8 (fallback Latin-1) byte sequence."""
    null_pos = raw.find(b"\x00")
    if null_pos != -1:
        raw = raw[:null_pos]
    try:
        return bytes(raw).decode("utf-8")
    except Exception:
        return bytes(raw).decode("latin-1", "replace")


class AcdiManufacturerSpace(MemorySpace):
    """
    ACDI Manufacturer Information Space (Space 0xFC / 252, Read-Only, 125 bytes).
    Fixed binary layout per NMRA S-9.7.4.3 backed by SnipData.
    """
    __slots__ = ("snip_data",)

    def __init__(self, snip_data):
        super().__init__(SPACE_ACDI_MFG, read_only=True)
        self.snip_data = snip_data

    def _encode_table(self) -> bytes:
        buf = bytearray(125)
        buf[0] = 0x04  # ACDI manufacturer version

        fields = (
            (1, 41, self.snip_data.manufacturer),
            (42, 41, self.snip_data.model),
            (83, 21, self.snip_data.hardware_version),
            (104, 21, self.snip_data.software_version),
        )
        for offset, width, text in fields:
            raw = (text or "").encode("ascii", "replace")[:width - 1]
            buf[offset:offset + len(raw)] = raw
        return bytes(buf)

    def read(self, address: int, count: int) -> bytes:
        if address >= 125:
            return b""
        return self._encode_table()[address:min(address + count, 125)]

    def get_size(self) -> int:
        return 125


class AcdiUserSpace(MemorySpace):
    """
    ACDI User Information Space (Space 0xFB / 251, Read/Write, 128 bytes).
    Fixed binary layout per NMRA S-9.7.4.3 synchronized live with SnipData.
    """
    __slots__ = ("snip_data", "filename", "on_write", "_data")

    def __init__(self, snip_data, filename: str = None, on_write=None):
        super().__init__(SPACE_ACDI_USER, read_only=False)
        self.snip_data = snip_data
        self.filename = filename
        self.on_write = on_write
        self._data = bytearray(128)
        self._sync_from_snip()
        if self.filename:
            self.load()

    def _sync_from_snip(self):
        self._data[0] = 2  # ACDI user info version
        uname = (self.snip_data.user_name or "").encode("utf-8")[:62]
        self._data[1:64] = uname + b"\x00" * (63 - len(uname))
        udesc = (self.snip_data.user_description or "").encode("utf-8")[:63]
        self._data[64:128] = udesc + b"\x00" * (64 - len(udesc))

    def _sync_to_snip(self):
        self.snip_data.user_name = _decode_cstr(self._data[1:64])
        self.snip_data.user_description = _decode_cstr(self._data[64:128])

    def load(self):
        """Load ACDI user space from flash storage if present."""
        try:
            with open(self.filename, "rb") as f:
                content = f.read(128)
                self._data[:len(content)] = content
                self._data[0] = 2
                self._sync_to_snip()
        except (OSError, Exception):
            pass

    def save(self):
        """Persist ACDI user space to flash storage."""
        if not self.filename:
            return
        try:
            with open(self.filename, "wb") as f:
                f.write(self._data)
        except (OSError, Exception):
            pass

    def read(self, address: int, count: int) -> bytes:
        if address >= 128:
            return b""
        self._sync_from_snip()
        return bytes(self._data[address:min(address + count, 128)])

    def write(self, address: int, data: bytes) -> bool:
        if address >= 128:
            return False
        self._sync_from_snip()
        limit = min(len(data), 128 - address)
        self._data[address:address + limit] = data[:limit]
        self._data[0] = 2
        self._sync_to_snip()

        if self.on_write:
            try:
                self.on_write(address, data[:limit])
            except Exception:
                pass

        if self.filename:
            self.save()
        return True

    def get_size(self) -> int:
        return 128


class MemoryConfigurationService:
    """
    Handles OpenLCB Memory Configuration Datagrams (Protocol 0x20).
    Routes reads and writes to registered MemorySpaces and replies via datagrams.
    """
    __slots__ = ("node_id", "alias_manager", "datagram_manager", "_spaces", "_on_restart_callback")
    def __init__(self, node_id, alias_manager, datagram_manager: DatagramManager):
        self.node_id = NodeID(node_id)
        self.alias_manager = alias_manager
        self.datagram_manager = datagram_manager
        self._spaces = {}  # space_id -> MemorySpace
        self._on_restart_callback = None

        # Register handler on datagram manager for protocol 0x20
        self.datagram_manager.register_handler(DATAGRAM_PROTOCOL_MEMORY_CONFIG, self.handle_datagram)

    @property
    def source_alias(self) -> int:
        return self.alias_manager.alias

    def register_space(self, space: MemorySpace):
        """Register a memory space (e.g. CDI Space 0xFF or Config Space 0xFD)."""
        self._spaces[space.space_id] = space

    def get_space(self, space_id: int) -> MemorySpace:
        """Get registered memory space by ID."""
        return self._spaces.get(space_id)

    def set_on_restart(self, callback):
        """Set callback invoked when a node restart/reboot command is received."""
        self._on_restart_callback = callback

    # --- Datagram Dispatcher ---

    async def handle_datagram(self, datagram: Datagram) -> bool:
        """
        Process incoming Memory Configuration Datagram.
        Returns True to ACK (Datagram OK) or False / error code to NAK.
        """
        data = datagram.data
        if len(data) < 2:
            return False

        cmd = data[1]

        # 1. Read Commands (0x40 - 0x43)
        if cmd in (CMD_READ_GENERIC, CMD_READ_CDI, CMD_READ_ALL_MEM, CMD_READ_CONFIG):
            asyncio.create_task(self._handle_read(datagram))
            return True

        # 2. Write Commands (0x00 - 0x03)
        elif cmd in (CMD_WRITE_GENERIC, CMD_WRITE_CDI, CMD_WRITE_ALL_MEM, CMD_WRITE_CONFIG):
            asyncio.create_task(self._handle_write(datagram))
            return True

        # 3. Get Configuration Options (0x80)
        elif cmd == CMD_GET_CONFIG_OPTIONS:
            asyncio.create_task(self._handle_get_options(datagram))
            return True

        # 4. Get Space Info (0x84)
        elif cmd == CMD_GET_SPACE_INFO:
            asyncio.create_task(self._handle_get_space_info(datagram))
            return True

        # 5. Restart / Reset (0xA8)
        elif cmd == CMD_RESTART:
            if self._on_restart_callback:
                try:
                    res = self._on_restart_callback()
                    if hasattr(res, "__await__"):
                        asyncio.create_task(res)
                except Exception:
                    pass
            return True

        return False

    # --- Command Handlers ---

    async def _handle_read(self, datagram: Datagram):
        """Process Memory Read Request and send Read Reply datagram."""
        data = datagram.data
        cmd = data[1]

        if len(data) < 6:
            return

        address = struct.unpack(">I", data[2:6])[0]

        # Determine Space and Count based on command byte
        if cmd == CMD_READ_GENERIC:
            if len(data) < 8:
                return
            space_id = data[6]
            count = data[7]
            reply_cmd = CMD_READ_REPLY_GENERIC
        else:
            count = data[6] if len(data) >= 7 else 64
            if cmd == CMD_READ_CDI:
                space_id = SPACE_CDI
                reply_cmd = CMD_READ_REPLY_CDI
            elif cmd == CMD_READ_ALL_MEM:
                space_id = SPACE_ALL_MEMORY
                reply_cmd = CMD_READ_REPLY_ALL_MEM
            elif cmd == CMD_READ_CONFIG:
                space_id = SPACE_CONFIG
                reply_cmd = CMD_READ_REPLY_CONFIG
            else:
                return

        # Read data from space
        space = self._spaces.get(space_id)
        if space is None:
            # Send Read Failure
            reply_payload = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_READ_REPLY_FAILURE])
            reply_payload.extend(struct.pack(">I", address))
            reply_payload.append(space_id)
            reply_payload.extend(struct.pack(">H", 0x1000))  # Permanent error: space unsupported
            await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply_payload))
            return

        # Maximum payload per datagram read reply is 64 bytes
        count = min(count, 64)
        read_bytes = space.read(address, count)

        # Assemble Read Reply Datagram
        reply = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, reply_cmd])
        reply.extend(struct.pack(">I", address))
        if reply_cmd == CMD_READ_REPLY_GENERIC:
            reply.append(space_id)
        reply.extend(read_bytes)

        await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply))

    async def _handle_write(self, datagram: Datagram):
        """Process Memory Write Request and send Write Reply or Failure."""
        data = datagram.data
        cmd = data[1]

        if len(data) < 6:
            return

        address = struct.unpack(">I", data[2:6])[0]

        if cmd == CMD_WRITE_GENERIC:
            if len(data) < 7:
                return
            space_id = data[6]
            write_payload = data[7:]
            reply_cmd = CMD_WRITE_REPLY_GENERIC
        else:
            write_payload = data[6:]
            if cmd == CMD_WRITE_CDI:
                space_id = SPACE_CDI
                reply_cmd = 0x11
            elif cmd == CMD_WRITE_ALL_MEM:
                space_id = SPACE_ALL_MEMORY
                reply_cmd = 0x12
            elif cmd == CMD_WRITE_CONFIG:
                space_id = SPACE_CONFIG
                reply_cmd = CMD_WRITE_REPLY_CONFIG
            else:
                return

        space = self._spaces.get(space_id)
        if space is None or space.read_only:
            # Send Write Failure Reply
            reply = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_WRITE_REPLY_FAILURE])
            reply.extend(struct.pack(">I", address))
            if cmd == CMD_WRITE_GENERIC:
                reply.append(space_id)
            reply.extend(struct.pack(">H", 0x1000))  # Permanent error
            await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply))
            return

        success = space.write(address, write_payload)
        if not success:
            reply = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, CMD_WRITE_REPLY_FAILURE])
            reply.extend(struct.pack(">I", address))
            if cmd == CMD_WRITE_GENERIC:
                reply.append(space_id)
            reply.extend(struct.pack(">H", 0x1000))
            await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply))
            return

        # Send Write Reply confirmation
        reply = bytearray([DATAGRAM_PROTOCOL_MEMORY_CONFIG, reply_cmd])
        reply.extend(struct.pack(">I", address))
        if reply_cmd == CMD_WRITE_REPLY_GENERIC:
            reply.append(space_id)
        await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply))

    async def _handle_get_options(self, datagram: Datagram):
        """Process Get Configuration Options (0x80) and send 0x82 Reply."""
        # Available options per S-9.7.4.2:
        # Bit 14: Unaligned reads supported (0x4000)
        # Bit 13: Unaligned writes supported (0x2000)
        # Bit 12: Space 0xFD supported in 0x43/0x03 commands (0x1000)
        # Bit 11: Space 0xFE supported in 0x42/0x02 commands (0x0800)
        # Bit 10: Space 0xFF supported in 0x41/0x01 commands (0x0400)
        # Bit 9:  Space 0xFC (ACDI Mfg) read supported (0x0200)
        # Bit 8:  Space 0xFB (ACDI User) read/write supported (0x0100)
        options_flags = 0x4000 | 0x2000 | 0x1000 | 0x0800 | 0x0400
        if SPACE_ACDI_MFG in self._spaces:
            options_flags |= 0x0200
        if SPACE_ACDI_USER in self._spaces:
            options_flags |= 0x0100

        # Write lengths:
        # Bit 7: Arbitrary write length (0x80)
        # Bit 3: 64-byte write (0x08)
        # Bit 2: 4-byte write (0x04)
        # Bit 1: 2-byte write (0x02)
        # Bit 0: 1-byte write (0x01)
        write_lengths = 0x8F

        # Highest and lowest available spaces
        if self._spaces:
            highest_space = max(self._spaces.keys())
            lowest_space = min(self._spaces.keys())
        else:
            highest_space = SPACE_CDI
            lowest_space = SPACE_CONFIG

        reply = bytearray([
            DATAGRAM_PROTOCOL_MEMORY_CONFIG,
            CMD_CONFIG_OPTIONS_REPLY,
        ])
        reply.extend(struct.pack(">H", options_flags))
        reply.append(write_lengths)
        reply.append(highest_space)
        reply.append(lowest_space)

        await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply))

    async def _handle_get_space_info(self, datagram: Datagram):
        """Process Get Space Info (0x84) and send 0x86 Reply."""
        data = datagram.data
        if len(data) < 3:
            return

        space_id = data[2]
        space = self._spaces.get(space_id)

        if space is not None:
            size = space.get_size()
            highest_addr = max(0, size - 1) if size > 0 else 0
            flags = 0x01 if space.read_only else 0x00  # Bit 0 = Read Only
        else:
            highest_addr = 0
            flags = 0x02  # Bit 1 = Space not present

        reply = bytearray([
            DATAGRAM_PROTOCOL_MEMORY_CONFIG,
            CMD_SPACE_INFO_REPLY,
            space_id,
        ])
        reply.extend(struct.pack(">I", highest_addr))
        reply.append(flags)

        await self.datagram_manager.send_datagram(datagram.source_alias, bytes(reply))
