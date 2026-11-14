"""
OpenLCB Datagram Transport Protocol.
Compliant with NMRA Standard S-9.7.3.2 and S-9.7.2.1.
Supports fragmentation and reassembly up to 72 bytes across 8-byte CAN frames with ACK/NAK flow control.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

import time
try:
    from micropython import const
except ImportError:
    def const(x):
        return x

from .can import CanFrame
from .node_id import NodeID


if hasattr(time, "ticks_ms"):
    def _now_ms() -> int:
        return time.ticks_ms()

    def _elapsed_ms(now: int, start: int) -> int:
        return time.ticks_diff(now, start)
else:
    def _now_ms() -> int:
        return int(time.monotonic() * 1000)

    def _elapsed_ms(now: int, start: int) -> int:
        return now - start


DATAGRAM_MAX_SIZE = const(72)
DATAGRAM_REASSEMBLY_TIMEOUT_MS = const(3000)

# Datagram Transport MTIs
MTI_DATAGRAM_OK       = const(0x0A28)  # Datagram Received OK (ACK)
MTI_DATAGRAM_REJECTED = const(0x0A48)  # Datagram Rejected (NAK)

# Standard Datagram Rejected Error Codes (S-9.7.3.2)
DATAGRAM_REJECTED_PERMANENT            = const(0x1000)  # Permanent error, do not retry
DATAGRAM_REJECTED_INFORMATION_LOGGED   = const(0x1010)  # Permanent error, info logged
DATAGRAM_REJECTED_SOURCE_NOT_PERMITTED = const(0x1020)  # Source not permitted
DATAGRAM_REJECTED_NOT_ACCEPTED         = const(0x1040)  # Datagrams not accepted
DATAGRAM_REJECTED_BUFFER_FULL          = const(0x2000)  # Temporary error: buffer full, resend OK
DATAGRAM_REJECTED_OUT_OF_ORDER         = const(0x2020)  # Temporary error: out of order
DATAGRAM_REJECTED_NO_TRANSPORT         = const(0x2040)  # Temporary error: no transport resource


class Datagram:
    """Represents a complete OpenLCB Datagram (up to 72 bytes)."""
    __slots__ = ("source_alias", "dest_alias", "data")

    def __init__(self, source_alias: int, dest_alias: int, data: bytes):
        self.source_alias = source_alias
        self.dest_alias = dest_alias
        self.data = bytes(data)

    @property
    def protocol_id(self) -> int:
        """First byte of datagram payload identifying the high-level protocol."""
        return self.data[0] if self.data else 0

    def __repr__(self) -> str:
        data_hex = self.data.hex().upper() if hasattr(self.data, "hex") else "".join(f"{b:02X}" for b in self.data)
        return f"<Datagram Src=0x{self.source_alias:03X} Dst=0x{self.dest_alias:03X} len={len(self.data)} data=[{data_hex}]>"


class DatagramManager:
    """
    Manages Datagram reception, reassembly, transmission, and flow control.
    """
    __slots__ = (
        "node_id",
        "alias_manager",
        "transport",
        "reassembly_timeout_ms",
        "_rx_buffers",
        "_rx_timestamps",
        "_handlers",
        "_default_handler",
        "_pending_tx",
    )

    def __init__(self, node_id, alias_manager, transport, reassembly_timeout_ms: int = DATAGRAM_REASSEMBLY_TIMEOUT_MS):
        self.node_id = NodeID(node_id)
        self.alias_manager = alias_manager
        self.transport = transport
        self.reassembly_timeout_ms = reassembly_timeout_ms

        self._rx_buffers = {}       # source_alias -> bytearray
        self._rx_timestamps = {}    # source_alias -> timestamp ms
        self._handlers = {}         # protocol_id -> callback
        self._default_handler = None
        self._pending_tx = {}       # dest_alias -> asyncio.Future / dict

    @property
    def source_alias(self) -> int:
        return self.alias_manager.alias

    def register_handler(self, protocol_id, callback):
        """
        Register a callback for datagrams with the specified protocol_id (first byte).
        If protocol_id is None, registers as the default fallback handler.
        Callback signature: callback(datagram) -> bool or (bool, error_code).
        Returning True (or None) sends Datagram OK; returning False sends Datagram Rejected.
        """
        if protocol_id is None:
            self._default_handler = callback
        else:
            self._handlers[protocol_id] = callback

    def unregister_handler(self, protocol_id):
        """Unregister handler for protocol_id."""
        if protocol_id is None:
            self._default_handler = None
        else:
            self._handlers.pop(protocol_id, None)

    # --- Datagram Reception & Reassembly ---

    def evict_stale_buffers(self, now_ms: int = None) -> list:
        """
        Evict incomplete multi-frame reassembly buffers older than reassembly_timeout_ms
        (per NMRA S-9.7.3.2) to prevent memory leaks from dropped transmissions.
        Returns a list of evicted source aliases.
        """
        if not self._rx_timestamps:
            return []

        now = _now_ms() if now_ms is None else now_ms
        timeout = self.reassembly_timeout_ms
        stale = [src for src, ts in self._rx_timestamps.items() if _elapsed_ms(now, ts) >= timeout]
        for src in stale:
            self._rx_buffers.pop(src, None)
            self._rx_timestamps.pop(src, None)
        return stale

    async def process_frame(self, frame: CanFrame):
        """
        Process incoming CAN frames for datagrams or datagram acknowledgments.
        """
        # 1. Check for Datagram ACKs or NAKs sent to our node
        if frame.is_openlcb_message and frame.destination_alias == self.source_alias:
            mti = frame.mti
            if mti == MTI_DATAGRAM_OK:
                self._handle_ack(frame.source_alias, ok=True, error_code=0)
                return
            elif mti == MTI_DATAGRAM_REJECTED:
                data = frame.data
                error_code = ((data[2] << 8) | data[3]) if len(data) >= 4 else 0
                self._handle_ack(frame.source_alias, ok=False, error_code=error_code)
                return

        # 2. Check for incoming Datagram frames addressed to our node
        if frame.is_datagram and frame.destination_alias == self.source_alias:
            if self._rx_timestamps:
                self.evict_stale_buffers()

            src = frame.source_alias

            if frame.is_datagram_only:
                # Single-frame datagram
                self._rx_buffers.pop(src, None)
                self._rx_timestamps.pop(src, None)
                await self._dispatch_datagram(Datagram(src, self.source_alias, frame.data))

            elif frame.is_datagram_first:
                # Start of multi-frame datagram
                self._rx_buffers[src] = bytearray(frame.data)
                self._rx_timestamps[src] = _now_ms()

            elif frame.is_datagram_middle:
                buf = self._rx_buffers.get(src)
                if buf is None:
                    # Received middle frame without first frame
                    await self._send_rejected(src, DATAGRAM_REJECTED_OUT_OF_ORDER)
                    return
                buf.extend(frame.data)
                if len(buf) > DATAGRAM_MAX_SIZE:
                    self._rx_buffers.pop(src, None)
                    self._rx_timestamps.pop(src, None)
                    await self._send_rejected(src, DATAGRAM_REJECTED_BUFFER_FULL)
                else:
                    self._rx_timestamps[src] = _now_ms()

            elif frame.is_datagram_final:
                buf = self._rx_buffers.pop(src, None)
                self._rx_timestamps.pop(src, None)
                if buf is None:
                    # Received final frame without start
                    await self._send_rejected(src, DATAGRAM_REJECTED_OUT_OF_ORDER)
                    return
                buf.extend(frame.data)
                if len(buf) > DATAGRAM_MAX_SIZE:
                    await self._send_rejected(src, DATAGRAM_REJECTED_BUFFER_FULL)
                    return
                await self._dispatch_datagram(Datagram(src, self.source_alias, bytes(buf)))

    async def _dispatch_datagram(self, datagram: Datagram):
        """Invoke registered handler and reply with Datagram OK or Datagram Rejected."""
        handler = self._handlers.get(datagram.protocol_id, self._default_handler)
        if handler is None:
            # No handler registered for this protocol
            await self._send_rejected(datagram.source_alias, DATAGRAM_REJECTED_PERMANENT)
            return

        try:
            res = handler(datagram)
            if hasattr(res, "__await__"):
                res = await res

            # Determine response
            if res is True or res is None:
                await self._send_ok(datagram.source_alias)
            elif res is False:
                await self._send_rejected(datagram.source_alias, DATAGRAM_REJECTED_PERMANENT)
            elif isinstance(res, tuple):
                ok, err_code = res
                if ok:
                    await self._send_ok(datagram.source_alias)
                else:
                    await self._send_rejected(datagram.source_alias, err_code)
            else:
                await self._send_ok(datagram.source_alias)
        except Exception:
            await self._send_rejected(datagram.source_alias, DATAGRAM_REJECTED_PERMANENT)

    async def _send_ok(self, dest_alias: int, reply_pending: bool = False):
        """Transmit Datagram Received OK (ACK)."""
        frame = CanFrame.make_datagram_ok(self.source_alias, dest_alias, reply_pending=reply_pending)
        await self.transport.send_frame(frame)

    async def _send_rejected(self, dest_alias: int, error_code: int):
        """Transmit Datagram Rejected (NAK)."""
        frame = CanFrame.make_datagram_rejected(self.source_alias, dest_alias, error_code=error_code)
        await self.transport.send_frame(frame)

    # --- Datagram Transmission ---

    def _handle_ack(self, sender_alias: int, ok: bool, error_code: int):
        """Resolve pending transmission future when ACK/NAK arrives."""
        entry = self._pending_tx.get(sender_alias)
        if entry is not None:
            entry["result"] = (ok, error_code)
            entry["event"].set()

    async def send_datagram(self, dest_alias: int, data: bytes, timeout_ms: int = 3000) -> bool:
        """
        Send a Datagram to dest_alias, fragmenting across 8-byte CAN frames if necessary.
        Blocks until Datagram OK or Datagram Rejected is received or timeout expires.
        Returns True on ACK (Datagram OK), False on NAK or timeout.
        """
        if len(data) > DATAGRAM_MAX_SIZE:
            raise ValueError(f"Datagram exceeds maximum size of {DATAGRAM_MAX_SIZE} bytes (got {len(data)})")

        if not self.alias_manager.is_permitted:
            raise RuntimeError("Cannot send datagram: Node alias is not in PERMITTED state")

        data = bytes(data)
        wait_event = asyncio.Event()
        entry = {"event": wait_event, "result": None}
        self._pending_tx[dest_alias] = entry

        try:
            data_len = len(data)
            if data_len <= 8:
                # Single-frame datagram
                frame = CanFrame.make_datagram_only(self.source_alias, dest_alias, data)
                await self.transport.send_frame(frame)
            else:
                # Multi-frame datagram fragmentation without intermediate list allocations
                await self.transport.send_frame(
                    CanFrame.make_datagram_first(self.source_alias, dest_alias, data[:8])
                )
                last_offset = ((data_len - 1) // 8) * 8
                for offset in range(8, last_offset, 8):
                    await self.transport.send_frame(
                        CanFrame.make_datagram_middle(self.source_alias, dest_alias, data[offset:offset + 8])
                    )
                await self.transport.send_frame(
                    CanFrame.make_datagram_final(self.source_alias, dest_alias, data[last_offset:])
                )

            # Wait for ACK/NAK
            try:
                await asyncio.wait_for(wait_event.wait(), timeout=timeout_ms / 1000.0)
                ok, err = entry["result"]
                return ok
            except asyncio.TimeoutError:
                return False
        finally:
            self._pending_tx.pop(dest_alias, None)
