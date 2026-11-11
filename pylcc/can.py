"""
OpenLCB CAN Frame Representation and Bit Field Definitions.
Compliant with NMRA Standard S-9.7.2.1 (CAN Frame Transfer).
"""

# Mask constants for 29-bit CAN identifiers
CAN_ID_MASK = 0x1FFFFFFF
OPENLCB_CAN_FRAME_FLAG = 0x10000000  # Bit 28: Reserved (must be 1 for OpenLCB)
FRAME_TYPE_MASK        = 0x08000000  # Bit 27: 0 = CAN control frame, 1 = OpenLCB message
VARIABLE_FIELD_MASK    = 0x07FFF000  # Bits 26-12: 15-bit variable field / MTI
VARIABLE_FIELD_SHIFT   = 12
SOURCE_ALIAS_MASK      = 0x00000FFF  # Bits 11-0: 12-bit source alias

# CAN Control Frame variable field values (Bits 26-12)
CONTROL_CID7_PREFIX = 0x7000  # CID Sequence 7 (Node ID bits 47-36)
CONTROL_CID6_PREFIX = 0x6000  # CID Sequence 6 (Node ID bits 35-24)
CONTROL_CID5_PREFIX = 0x5000  # CID Sequence 5 (Node ID bits 23-12)
CONTROL_CID4_PREFIX = 0x4000  # CID Sequence 4 (Node ID bits 11-0)
CONTROL_RID         = 0x0700  # Reserve ID
CONTROL_AMD         = 0x0701  # Alias Map Definition
CONTROL_AME         = 0x0702  # Alias Mapping Enquiry
CONTROL_AMR         = 0x0703  # Alias Map Reset


class CanFrame:
    """
    Represents a CAN 2.0B frame with up to 8 bytes of payload.
    Supports OpenLCB 29-bit extended identifier inspection and construction.
    """
    __slots__ = ("can_id", "data", "is_extended", "is_rtr")

    def __init__(self, can_id: int, data=b"", is_extended: bool = True, is_rtr: bool = False):
        self.can_id = can_id & CAN_ID_MASK
        if isinstance(data, (bytes, bytearray)):
            self.data = bytes(data)
        elif hasattr(data, "__iter__"):
            self.data = bytes(data)
        else:
            self.data = bytes(data or b"")

        if len(self.data) > 8:
            raise ValueError(f"CAN frame data cannot exceed 8 bytes (got {len(self.data)})")

        self.is_extended = bool(is_extended)
        self.is_rtr = bool(is_rtr)

    @property
    def source_alias(self) -> int:
        """The 12-bit Source Alias from the CAN ID."""
        return self.can_id & SOURCE_ALIAS_MASK

    @property
    def variable_field(self) -> int:
        """The 15-bit Variable Field (bits 26-12)."""
        return (self.can_id & VARIABLE_FIELD_MASK) >> VARIABLE_FIELD_SHIFT

    @property
    def is_openlcb(self) -> bool:
        """True if bit 28 is set (OpenLCB CAN standard)."""
        return bool(self.can_id & OPENLCB_CAN_FRAME_FLAG)

    @property
    def is_openlcb_message(self) -> bool:
        """True if bit 28=1 and bit 27=1 (OpenLCB message frame)."""
        return (self.can_id & (OPENLCB_CAN_FRAME_FLAG | FRAME_TYPE_MASK)) == (OPENLCB_CAN_FRAME_FLAG | FRAME_TYPE_MASK)

    @property
    def is_can_control(self) -> bool:
        """True if bit 28=1 and bit 27=0 (CAN-specific control frame)."""
        return (self.can_id & (OPENLCB_CAN_FRAME_FLAG | FRAME_TYPE_MASK)) == OPENLCB_CAN_FRAME_FLAG

    @property
    def is_cid(self) -> bool:
        """True if this is a Check ID (CID) frame (CID 7, 6, 5, or 4)."""
        if not self.is_can_control:
            return False
        seq = (self.variable_field >> 12) & 0x7
        return seq in (4, 5, 6, 7)

    @property
    def cid_sequence(self) -> int:
        """CID sequence number (7 down to 4), or None if not a CID frame."""
        if not self.is_cid:
            return None
        return (self.variable_field >> 12) & 0x7

    @property
    def cid_chunk(self) -> int:
        """12-bit Node ID chunk carried in this CID frame."""
        if not self.is_cid:
            return None
        return self.variable_field & 0x0FFF

    @property
    def is_rid(self) -> bool:
        """True if Reserve ID (RID) frame."""
        return self.is_can_control and self.variable_field == CONTROL_RID

    @property
    def is_amd(self) -> bool:
        """True if Alias Map Definition (AMD) frame."""
        return self.is_can_control and self.variable_field == CONTROL_AMD

    @property
    def is_ame(self) -> bool:
        """True if Alias Mapping Enquiry (AME) frame."""
        return self.is_can_control and self.variable_field == CONTROL_AME

    @property
    def is_amr(self) -> bool:
        """True if Alias Map Reset (AMR) frame."""
        return self.is_can_control and self.variable_field == CONTROL_AMR

    # --- OpenLCB Message properties (Global & Addressed) ---

    @property
    def mti(self) -> int:
        """
        12-bit Message Type Indicator (MTI) for OpenLCB message frames.
        Returns 0 if not an OpenLCB message frame.
        """
        if not self.is_openlcb_message:
            return 0
        return self.variable_field & 0x0FFF

    @property
    def is_addressed(self) -> bool:
        """
        True if this is an Addressed OpenLCB Message (bit 3 of MTI is set).
        """
        return self.is_openlcb_message and bool(self.mti & 0x008)

    @property
    def destination_alias(self):
        """
        12-bit Destination Alias for Addressed OpenLCB Messages.
        Extracted from the first two bytes of data (fddd format).
        Returns None if not an addressed message or data is too short.
        """
        if not self.is_addressed or len(self.data) < 2:
            return None
        return int.from_bytes(self.data[:2], "big") & 0x0FFF

    @property
    def destination_payload(self) -> bytes:
        """
        Data payload stripped of the 2-byte destination alias header for addressed messages.
        For global messages, returns self.data.
        """
        if self.is_addressed and len(self.data) >= 2:
            return self.data[2:]
        return self.data

    # --- Constructors for OpenLCB CAN control frames ---

    @classmethod
    def make_cid(cls, sequence: int, nid_chunk: int, alias: int) -> "CanFrame":
        """
        Create a Check ID (CID) frame.
        :param sequence: 7, 6, 5, or 4
        :param nid_chunk: 12-bit fragment of the 48-bit Node ID
        :param alias: 12-bit proposed alias
        """
        if sequence not in (4, 5, 6, 7):
            raise ValueError(f"CID sequence must be 4, 5, 6, or 7, got {sequence}")
        var_field = ((sequence & 0x7) << 12) | (nid_chunk & 0x0FFF)
        can_id = OPENLCB_CAN_FRAME_FLAG | (var_field << VARIABLE_FIELD_SHIFT) | (alias & SOURCE_ALIAS_MASK)
        return cls(can_id, b"")

    @classmethod
    def make_rid(cls, alias: int) -> "CanFrame":
        """Create a Reserve ID (RID) frame."""
        can_id = OPENLCB_CAN_FRAME_FLAG | (CONTROL_RID << VARIABLE_FIELD_SHIFT) | (alias & SOURCE_ALIAS_MASK)
        return cls(can_id, b"")

    @classmethod
    def make_amd(cls, alias: int, node_id_bytes: bytes) -> "CanFrame":
        """
        Create an Alias Map Definition (AMD) frame.
        Carries the full 6-byte (48-bit) Node ID in the payload.
        """
        if len(node_id_bytes) != 6:
            raise ValueError(f"AMD frame requires 6-byte Node ID, got {len(node_id_bytes)}")
        can_id = OPENLCB_CAN_FRAME_FLAG | (CONTROL_AMD << VARIABLE_FIELD_SHIFT) | (alias & SOURCE_ALIAS_MASK)
        return cls(can_id, node_id_bytes)

    @classmethod
    def make_ame(cls, alias: int, target_node_id: bytes = b"") -> "CanFrame":
        """
        Create an Alias Mapping Enquiry (AME) frame.
        Payload can be empty (enquiry to all nodes) or 6 bytes (enquiry for specific Node ID).
        """
        can_id = OPENLCB_CAN_FRAME_FLAG | (CONTROL_AME << VARIABLE_FIELD_SHIFT) | (alias & SOURCE_ALIAS_MASK)
        return cls(can_id, target_node_id)

    @classmethod
    def make_amr(cls, alias: int, node_id_bytes: bytes) -> "CanFrame":
        """
        Create an Alias Map Reset (AMR) frame.
        Carries the full 6-byte Node ID being released.
        """
        if len(node_id_bytes) != 6:
            raise ValueError(f"AMR frame requires 6-byte Node ID, got {len(node_id_bytes)}")
        can_id = OPENLCB_CAN_FRAME_FLAG | (CONTROL_AMR << VARIABLE_FIELD_SHIFT) | (alias & SOURCE_ALIAS_MASK)
        return cls(can_id, node_id_bytes)

    # --- Constructors for OpenLCB Message Frames ---

    @classmethod
    def make_global_message(cls, mti: int, source_alias: int, data: bytes = b"") -> "CanFrame":
        """
        Create a Global OpenLCB Message Frame (Format 1, bit 24 = 1).
        MTI is encoded in bits 23-12.
        """
        can_id = (
            OPENLCB_CAN_FRAME_FLAG
            | FRAME_TYPE_MASK
            | (0x01 << 24)  # Format Type 1 (OpenLCB Message)
            | ((mti & 0x0FFF) << VARIABLE_FIELD_SHIFT)
            | (source_alias & SOURCE_ALIAS_MASK)
        )
        return cls(can_id, data)

    @classmethod
    def make_addressed_message(cls, mti: int, source_alias: int, dest_alias: int, payload: bytes = b"") -> "CanFrame":
        """
        Create an Addressed OpenLCB Message Frame.
        MTI is encoded in bits 23-12 (with bit 3 of MTI set).
        First two bytes of data contain the 12-bit destination alias.
        """
        mti_addressed = mti | 0x008  # Ensure addressed bit is set
        can_id = (
            OPENLCB_CAN_FRAME_FLAG
            | FRAME_TYPE_MASK
            | (0x01 << 24)
            | ((mti_addressed & 0x0FFF) << VARIABLE_FIELD_SHIFT)
            | (source_alias & SOURCE_ALIAS_MASK)
        )
        data = (dest_alias & SOURCE_ALIAS_MASK).to_bytes(2, "big") + payload
        return cls(can_id, data)

    def __eq__(self, other) -> bool:
        if not isinstance(other, CanFrame):
            return False
        return (
            self.can_id == other.can_id
            and self.data == other.data
            and self.is_extended == other.is_extended
            and self.is_rtr == other.is_rtr
        )

    def __repr__(self) -> str:
        flags = []
        if self.is_extended:
            flags.append("EXT")
        if self.is_rtr:
            flags.append("RTR")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        data_hex = self.data.hex().upper() if hasattr(self.data, "hex") else "".join(f"{b:02X}" for b in self.data)
        return f"<CanFrame ID=0x{self.can_id:08X}{flag_str} len={len(self.data)} data=[{data_hex}]>"
