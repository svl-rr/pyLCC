"""
OpenLCB 48-bit Node ID Representation.
Compliant with OpenLCB Unique Identifier Architecture (S-9.7.0.1).
"""


class NodeID:
    """
    Represents a unique 48-bit (6-byte) OpenLCB / LCC Node ID.
    Can be initialized from an integer, 6-byte bytes/bytearray, or hex-formatted string.
    """
    __slots__ = ("_value",)

    def __init__(self, value):
        if isinstance(value, int):
            if not (0 <= value <= 0xFFFFFFFFFFFF):
                raise ValueError(f"NodeID integer out of 48-bit range: 0x{value:X}")
            self._value = value
        elif isinstance(value, (bytes, bytearray)):
            if len(value) != 6:
                raise ValueError(f"NodeID bytes must be exactly 6 bytes (got {len(value)})")
            self._value = int.from_bytes(value, "big")
        elif isinstance(value, str):
            # Supports '05.01.01.01.14.00' or '05:01:01:01:14:00' or '050101011400'
            clean = value.replace(".", "").replace(":", "").replace("-", "").strip()
            if len(clean) != 12:
                raise ValueError(f"Invalid NodeID string format: {value!r}")
            self._value = int(clean, 16)
        elif isinstance(value, NodeID):
            self._value = value._value
        else:
            raise TypeError(f"Cannot create NodeID from type {type(value).__name__}")

    @property
    def value(self) -> int:
        return self._value

    def to_bytes(self) -> bytes:
        """Return Node ID as 6 big-endian bytes."""
        return self._value.to_bytes(6, "big")

    # 12-bit chunks for CAN Check ID (CID) frames
    @property
    def chunk_7(self) -> int:
        """Bits 47-36 (used in CID 7)."""
        return (self._value >> 36) & 0x0FFF

    @property
    def chunk_6(self) -> int:
        """Bits 35-24 (used in CID 6)."""
        return (self._value >> 24) & 0x0FFF

    @property
    def chunk_5(self) -> int:
        """Bits 23-12 (used in CID 5)."""
        return (self._value >> 12) & 0x0FFF

    @property
    def chunk_4(self) -> int:
        """Bits 11-0 (used in CID 4)."""
        return self._value & 0x0FFF

    def get_chunk(self, sequence: int) -> int:
        """Get 12-bit chunk for CID sequence number (7, 6, 5, or 4)."""
        if sequence == 7:
            return self.chunk_7
        elif sequence == 6:
            return self.chunk_6
        elif sequence == 5:
            return self.chunk_5
        elif sequence == 4:
            return self.chunk_4
        else:
            raise ValueError(f"CID sequence must be 4..7, got {sequence}")

    def __eq__(self, other) -> bool:
        if isinstance(other, NodeID):
            return self._value == other._value
        elif isinstance(other, int):
            return self._value == other
        elif isinstance(other, (bytes, bytearray)):
            return self.to_bytes() == bytes(other)
        return False

    def __hash__(self) -> int:
        return hash(self._value)

    def __repr__(self) -> str:
        b = self.to_bytes()
        formatted = ".".join(f"{byte:02X}" for byte in b)
        return f"NodeID('{formatted}')"

    def __str__(self) -> str:
        b = self.to_bytes()
        return ".".join(f"{byte:02X}" for byte in b)
