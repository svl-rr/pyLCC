"""
OpenLCB Simple Node Information Protocol (SNIP) & Protocol Support (PIP).
Compliant with NMRA Standard S-9.7.4.3 (SNIP) and S-9.7.3 (PIP).
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

try:
    from micropython import const
except ImportError:
    def const(x):
        return x

from .can import CanFrame
from .node_id import NodeID


# Protocol Support Inquiry / Reply MTIs (PIP)
MTI_PROTOCOL_SUPPORT_INQUIRY = const(0x0828)
MTI_PROTOCOL_SUPPORT_REPLY   = const(0x0668)

# Simple Node Information Protocol MTIs (SNIP)
MTI_SIMPLE_NODE_IDENT_INFO_REQUEST = const(0x0DE8)
MTI_SIMPLE_NODE_IDENT_INFO_REPLY   = const(0x0A08)

# Standard Protocol Identification Flags (PIP 24-bit mask)
PIP_SIMPLE_NODE      = const(0x800000)  # Simple Node Protocol
PIP_DATAGRAM         = const(0x400000)  # Datagram Protocol
PIP_STREAM           = const(0x200000)  # Stream Protocol
PIP_MEMORY_CONFIG    = const(0x100000)  # Memory Configuration Protocol
PIP_RESERVATION      = const(0x080000)  # Reservation Protocol
PIP_EVENT_EXCHANGE   = const(0x040000)  # Event Exchange Protocol
PIP_IDENT_PROTOCOL   = const(0x020000)  # Identification Protocol
PIP_TEACH_LEARN      = const(0x010000)  # Teaching/Learning Protocol
PIP_REMOTE_BUTTON    = const(0x008000)  # Remote Button Protocol
PIP_ACDI             = const(0x004000)  # Abbreviated CDI
PIP_DISPLAY          = const(0x002000)  # Display Protocol
PIP_SNIP             = const(0x001000)  # Simple Node Information Protocol
PIP_CDI              = const(0x000800)  # Configuration Description Information
PIP_TRACTION_CONTROL = const(0x000400)  # Train Control Protocol


class SnipData:
    """Holds the human-readable identification strings for a node."""
    __slots__ = (
        "manufacturer",
        "model",
        "hardware_version",
        "software_version",
        "user_name",
        "user_description",
    )
    def __init__(
        self,
        manufacturer: str = "svl-rr",
        model: str = "pyLCC Node",
        hardware_version: str = "v1.0",
        software_version: str = "0.1.0",
        user_name: str = "",
        user_description: str = "",
    ):
        self.manufacturer = manufacturer
        self.model = model
        self.hardware_version = hardware_version
        self.software_version = software_version
        self.user_name = user_name
        self.user_description = user_description

    def encode(self) -> bytes:
        """
        Encode SNIP data according to NMRA S-9.7.4.3:
        [Version: 0x04]
        [Manufacturer\0]
        [Model\0]
        [Hardware Version\0]
        [Software Version\0]
        [User Info Version: 0x01]
        [User Name\0]
        [User Description\0]
        """
        buf = bytearray()
        # Manufacturer info version
        buf.append(0x04)
        buf.extend(self.manufacturer.encode("ascii", "replace"))
        buf.append(0x00)
        buf.extend(self.model.encode("ascii", "replace"))
        buf.append(0x00)
        buf.extend(self.hardware_version.encode("ascii", "replace"))
        buf.append(0x00)
        buf.extend(self.software_version.encode("ascii", "replace"))
        buf.append(0x00)

        # User info version
        buf.append(0x01)
        buf.extend(self.user_name.encode("utf-8", "replace"))
        buf.append(0x00)
        buf.extend(self.user_description.encode("utf-8", "replace"))
        buf.append(0x00)
        return bytes(buf)

    @classmethod
    def decode(cls, data: bytes) -> "SnipData":
        """Decode raw bytes into a SnipData instance."""
        if not data:
            return cls()

        idx = 0
        # Manufacturer info version byte
        mfg_version = data[idx]
        idx += 1

        strings = []
        for _ in range(4):
            end = data.find(b"\x00", idx)
            if end == -1:
                break
            strings.append(data[idx:end].decode("ascii", "replace"))
            idx = end + 1

        # User info version byte (if present)
        user_strings = []
        if idx < len(data):
            user_version = data[idx]
            idx += 1
            for _ in range(2):
                end = data.find(b"\x00", idx)
                if end == -1:
                    break
                user_strings.append(data[idx:end].decode("utf-8", "replace"))
                idx = end + 1

        mfg = strings[0] if len(strings) > 0 else ""
        model = strings[1] if len(strings) > 1 else ""
        hw = strings[2] if len(strings) > 2 else ""
        sw = strings[3] if len(strings) > 3 else ""
        uname = user_strings[0] if len(user_strings) > 0 else ""
        udesc = user_strings[1] if len(user_strings) > 1 else ""

        return cls(
            manufacturer=mfg,
            model=model,
            hardware_version=hw,
            software_version=sw,
            user_name=uname,
            user_description=udesc,
        )


class SnipService:
    """
    Handles Simple Node Information Protocol (SNIP) requests and Protocol Identification (PIP).
    """
    __slots__ = ("node_id", "alias_manager", "transport", "snip_data", "protocols")
    def __init__(self, node_id, alias_manager, transport, snip_data: SnipData = None, protocols: int = None):
        self.node_id = NodeID(node_id)
        self.alias_manager = alias_manager
        self.transport = transport
        self.snip_data = snip_data or SnipData()

        # Default supported protocols bitmask
        if protocols is not None:
            self.protocols = protocols
        else:
            self.protocols = (
                PIP_SIMPLE_NODE
                | PIP_DATAGRAM
                | PIP_EVENT_EXCHANGE
                | PIP_ACDI
                | PIP_SNIP
                | PIP_MEMORY_CONFIG
                | PIP_CDI
            )

    @property
    def source_alias(self) -> int:
        return self.alias_manager.alias

    async def process_frame(self, frame: CanFrame):
        """Handle incoming PIP and SNIP inquiries."""
        if not frame.is_openlcb_message:
            return

        if frame.destination_alias != self.source_alias:
            return

        mti = frame.mti

        # 1. Protocol Support Inquiry (PIP)
        if mti == MTI_PROTOCOL_SUPPORT_INQUIRY:
            pip_bytes = self.protocols.to_bytes(3, "big")
            reply_frame = CanFrame.make_addressed_message(
                MTI_PROTOCOL_SUPPORT_REPLY,
                source_alias=self.source_alias,
                dest_alias=frame.source_alias,
                payload=pip_bytes
            )
            await self.transport.send_frame(reply_frame)

        # 2. Simple Node Ident Info Request (SNIP)
        elif mti == MTI_SIMPLE_NODE_IDENT_INFO_REQUEST:
            raw_snip = self.snip_data.encode()
            # Send in chunks of up to 6 bytes per addressed CAN frame
            chunk_size = 6
            for i in range(0, len(raw_snip), chunk_size):
                chunk = raw_snip[i:i + chunk_size]
                reply_frame = CanFrame.make_addressed_message(
                    MTI_SIMPLE_NODE_IDENT_INFO_REPLY,
                    source_alias=self.source_alias,
                    dest_alias=frame.source_alias,
                    payload=chunk
                )
                await self.transport.send_frame(reply_frame)
