"""
OpenLCB 64-bit Event ID Representation.
Compliant with OpenLCB Event Transport Standard (S-9.7.3.1).
"""

from .node_id import NodeID


class EventID:
    """
    Represents an OpenLCB 64-bit (8-byte) globally unique Event ID.
    Used for Producer/Consumer events: sensors, turnouts, block occupancy, signals, etc.
    Typically structured as a 48-bit Node ID followed by a 16-bit event counter.
    """
    __slots__ = ("_value", "_bytes")

    def __init__(self, value, index: int = None):
        if index is not None:
            # Construct from NodeID (or node ID value) + 16-bit index
            base_node = NodeID(value)
            if not (0 <= index <= 0xFFFF):
                raise ValueError(f"Event index must be a 16-bit unsigned integer (got {index})")
            self._value = (base_node.value << 16) | index
            self._bytes = self._value.to_bytes(8, "big")
        elif isinstance(value, int):
            if not (0 <= value <= 0xFFFFFFFFFFFFFFFF):
                raise ValueError(f"EventID integer out of 64-bit range: 0x{value:X}")
            self._value = value
            self._bytes = value.to_bytes(8, "big")
        elif isinstance(value, (bytes, bytearray)):
            if len(value) != 8:
                raise ValueError(f"EventID bytes must be exactly 8 bytes (got {len(value)})")
            self._bytes = bytes(value)
            self._value = int.from_bytes(self._bytes, "big")
        elif isinstance(value, str):
            # Supports '05.01.01.01.14.00.00.01' or '05:01:01:01:14:00:00:01' or hex string
            clean = value.replace(".", "").replace(":", "").replace("-", "").strip()
            if len(clean) != 16:
                raise ValueError(f"Invalid EventID string format: {value!r}")
            self._value = int(clean, 16)
            self._bytes = self._value.to_bytes(8, "big")
        elif isinstance(value, EventID):
            self._value = value._value
            self._bytes = value._bytes
        else:
            raise TypeError(f"Cannot create EventID from type {type(value).__name__}")

    @property
    def value(self) -> int:
        return self._value

    def __int__(self) -> int:
        return self._value

    def to_bytes(self) -> bytes:
        """Return Event ID as 8 big-endian bytes."""
        return self._bytes

    @property
    def node_id(self) -> NodeID:
        """Extract the 48-bit Node ID portion (upper 6 bytes)."""
        return NodeID(self._value >> 16)

    @property
    def event_index(self) -> int:
        """Extract the 16-bit event index portion (lower 2 bytes)."""
        return self._value & 0xFFFF

    @classmethod
    def from_node_id(cls, node_id, index: int) -> "EventID":
        """Convenience factory: derive EventID from a NodeID and 16-bit integer index."""
        return cls(node_id, index)

    def __eq__(self, other) -> bool:
        if isinstance(other, EventID):
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
        return f"EventID('{formatted}')"

    def __str__(self) -> str:
        b = self.to_bytes()
        return ".".join(f"{byte:02X}" for byte in b)
