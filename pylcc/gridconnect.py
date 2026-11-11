"""
GridConnect Protocol Serialization and Streaming Parser.
Encodes and decodes standard CAN frames in GridConnect ASCII format (:X...N...;).
"""

from .can import CanFrame


def encode_gridconnect(frame: CanFrame) -> str:
    """
    Encode a CanFrame into a GridConnect ASCII string.
    Example output: ':X195B422AN0102030405060708;'
    """
    frame_type = "X" if frame.is_extended else "S"
    id_format = f"{frame.can_id:08X}" if frame.is_extended else f"{frame.can_id:03X}"
    rtr_flag = "R" if frame.is_rtr else "N"
    
    if frame.data:
        data_hex = "".join(f"{b:02X}" for b in frame.data)
    else:
        data_hex = ""
        
    return f":{frame_type}{id_format}{rtr_flag}{data_hex};"


def decode_gridconnect(text: str) -> CanFrame:
    """
    Decode a single GridConnect ASCII string into a CanFrame.
    Raises ValueError if the string is malformed.
    """
    text = text.strip()
    if not (text.startswith(":") and text.endswith(";")):
        raise ValueError(f"Malformed GridConnect frame (must start with ':' and end with ';'): {text!r}")

    content = text[1:-1]
    if len(content) < 4:
        raise ValueError(f"GridConnect frame too short: {text!r}")

    # Format char
    fmt = content[0].upper()
    if fmt == "X":
        is_extended = True
        id_len = 8
    elif fmt == "S":
        is_extended = False
        id_len = 3
    else:
        raise ValueError(f"Unknown frame identifier type '{fmt}' in {text!r}")

    if len(content) < 1 + id_len + 1:
        raise ValueError(f"Incomplete GridConnect header in {text!r}")

    # Parse CAN ID
    try:
        can_id = int(content[1:1 + id_len], 16)
    except ValueError as e:
        raise ValueError(f"Invalid hex in CAN ID in {text!r}") from e

    # RTR flag
    flag = content[1 + id_len].upper()
    if flag == "N":
        is_rtr = False
    elif flag == "R":
        is_rtr = True
    else:
        raise ValueError(f"Unknown RTR flag '{flag}' in {text!r}")

    # Data bytes
    data_hex = content[1 + id_len + 1:]
    if len(data_hex) % 2 != 0:
        raise ValueError(f"Odd number of hex nibbles in data: {data_hex!r}")

    data_bytes = bytearray()
    for i in range(0, len(data_hex), 2):
        byte_val = int(data_hex[i:i + 2], 16)
        data_bytes.append(byte_val)

    if len(data_bytes) > 8:
        raise ValueError(f"Data length exceeds 8 bytes: {len(data_bytes)} bytes in {text!r}")

    return CanFrame(can_id=can_id, data=bytes(data_bytes), is_extended=is_extended, is_rtr=is_rtr)


class GridConnectParser:
    """
    Stream parser for GridConnect ASCII frames.
    Buffers incoming bytes or string chunks and yields parsed CanFrame instances.
    Resilient to whitespace, newlines, and frame boundary fragmentation.
    """
    def __init__(self):
        self._buffer = []
        self._in_frame = False

    def feed(self, chunk) -> list:
        """
        Feed a chunk of bytes or string into the parser.
        Returns a list of parsed CanFrame objects.
        """
        if isinstance(chunk, (bytes, bytearray)):
            # Convert bytes to string (ASCII)
            chunk = chunk.decode("latin1", errors="ignore")

        frames = []
        for char in chunk:
            if char == ":":
                # Start of a new frame. If already buffering, drop previous incomplete frame.
                self._buffer = [":"]
                self._in_frame = True
            elif char == ";":
                if self._in_frame:
                    self._buffer.append(";")
                    frame_str = "".join(self._buffer)
                    self._buffer = []
                    self._in_frame = False
                    try:
                        frames.append(decode_gridconnect(frame_str))
                    except ValueError:
                        # Drop corrupt frame silently or log
                        pass
            elif self._in_frame:
                # Discard whitespace inside or outside frame
                if not char.isspace():
                    self._buffer.append(char)
                    # Safety check: prevent runaway buffers
                    if len(self._buffer) > 40:
                        self._buffer = []
                        self._in_frame = False

        return frames

    def reset(self):
        """Reset internal buffer state."""
        self._buffer = []
        self._in_frame = False
