"""
GridConnect Protocol Serialization and Streaming Parser.
Encodes and decodes standard CAN frames in GridConnect ASCII format (:X...N...;).
"""

try:
    import binascii
except ImportError:
    import ubinascii as binascii

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
        data_hex = binascii.hexlify(frame.data).decode("ascii").upper()
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

    if data_hex:
        try:
            data_bytes = binascii.unhexlify(data_hex)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid hex in data payload: {data_hex!r}") from e
    else:
        data_bytes = b""

    if len(data_bytes) > 8:
        raise ValueError(f"Data length exceeds 8 bytes: {len(data_bytes)} bytes in {text!r}")

    return CanFrame(can_id=can_id, data=data_bytes, is_extended=is_extended, is_rtr=is_rtr)


class GridConnectParser:
    """
    Stream parser for GridConnect ASCII frames.
    Buffers incoming bytes or string chunks and yields parsed CanFrame instances.
    Resilient to whitespace, newlines, and frame boundary fragmentation.
    """
    __slots__ = ("_buffer",)

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, chunk) -> list:
        """
        Feed a chunk of bytes or string into the parser.
        Returns a list of parsed CanFrame objects.
        """
        if not chunk:
            return []

        if isinstance(chunk, str):
            chunk = chunk.encode("latin1")

        buf = self._buffer
        buf.extend(chunk)

        frames = []
        while buf:
            start = buf.find(b":")
            if start == -1:
                buf.clear()
                break
            if start > 0:
                del buf[:start]

            end = buf.find(b";", 1)
            if end == -1:
                # If a newer ':' exists in the partial buffer, drop the earlier incomplete one
                last_colon = buf.rfind(b":")
                if last_colon > 0:
                    del buf[:last_colon]
                # Safety check: prevent runaway buffers
                if len(buf) > 40:
                    buf.clear()
                break

            # Handle nested/restarted ':' before the terminating ';'
            last_colon = buf.rfind(b":", 0, end)
            if last_colon > 0:
                del buf[:last_colon]
                end -= last_colon

            raw_frame = buf[:end + 1].decode("latin1")
            del buf[:end + 1]

            if " " in raw_frame or "\t" in raw_frame or "\r" in raw_frame or "\n" in raw_frame:
                raw_frame = "".join(c for c in raw_frame if not c.isspace())

            try:
                frames.append(decode_gridconnect(raw_frame))
            except ValueError:
                # Drop corrupt frame silently
                pass

        return frames

    def reset(self):
        """Reset internal buffer state."""
        self._buffer.clear()
