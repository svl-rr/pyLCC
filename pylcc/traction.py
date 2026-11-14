"""
OpenLCB Traction Control Protocol Engine (NMRA Standard S-9.7.4.4 / TN-9.7.4.4).
Supports IEEE 754 float16 speed/direction, functions (F0-F28+), E-Stop,
controller assignment, consist listener management, and reservations.
"""

import math
import struct

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


# Traction Control Message Type Indicators (Addressed MTIs)
MTI_TRACTION_CONTROL_COMMAND = const(0x05EB)
MTI_TRACTION_CONTROL_REPLY   = const(0x01E9)

# Well-Known Event ID produced by Train Nodes (NMRA S-9.7.4.4)
EVENT_IS_TRAIN = "01.01.00.00.00.00.03.03"

# Instruction Bytes (Byte 0; bit 7 = 0x80 marks forwarded consist command)
TRACTION_SET_SPEED         = const(0x00)
TRACTION_SET_FUNCTION      = const(0x01)
TRACTION_EMERGENCY_STOP    = const(0x02)
TRACTION_QUERY_SPEED       = const(0x10)
TRACTION_QUERY_FUNCTION    = const(0x11)
TRACTION_CONTROLLER_CONFIG = const(0x20)
TRACTION_LISTENER_CONFIG   = const(0x30)
TRACTION_MGMT              = const(0x40)

TRACTION_LISTENER_BIT      = const(0x80)

# Controller Configuration Subcommands (Byte 1 when Byte 0 == 0x20)
TRACTION_CTRL_ASSIGN  = const(0x01)
TRACTION_CTRL_RELEASE = const(0x02)
TRACTION_CTRL_QUERY   = const(0x03)
TRACTION_CTRL_NOTIFY  = const(0x04)

# Listener / Consist Configuration Subcommands (Byte 1 when Byte 0 == 0x30)
TRACTION_LISTENER_ATTACH = const(0x01)
TRACTION_LISTENER_DETACH = const(0x02)
TRACTION_LISTENER_QUERY  = const(0x03)

# Consist Listener Flags
LISTENER_FLAG_REVERSE = const(0x02)
LISTENER_FLAG_LINK_F0 = const(0x04)
LISTENER_FLAG_LINK_FN = const(0x08)
LISTENER_FLAG_HIDE    = const(0x80)

# Management Subcommands (Byte 1 when Byte 0 == 0x40)
TRACTION_MGMT_RESERVE = const(0x01)
TRACTION_MGMT_RELEASE = const(0x02)
TRACTION_MGMT_NOOP    = const(0x03)


def encode_float16(speed_mps: float, reverse: bool = False) -> bytes:
    """
    Encode speed in meters/second and direction into 2-byte IEEE 754 binary16.
    Preserves signed +0.0 (0x0000 = Forward Stop) and -0.0 (0x8000 = Reverse Stop).
    """
    if math.isnan(speed_mps):
        return b"\x7f\xff"

    if speed_mps < 0:
        reverse = True
        speed_mps = -speed_mps

    sign = 0x8000 if reverse else 0x0000
    if speed_mps == 0.0:
        return bytes(((sign >> 8) & 0xFF, sign & 0xFF))

    if math.isinf(speed_mps):
        h = sign | 0x7C00
        return bytes(((h >> 8) & 0xFF, h & 0xFF))

    f32 = struct.unpack(">I", struct.pack(">f", speed_mps))[0]
    exp32 = (f32 >> 23) & 0xFF
    mant32 = f32 & 0x7FFFFF
    exp16 = exp32 - 127 + 15

    if exp16 >= 0x1F:
        # Clamp finite overflow to max representable float16 (65504.0)
        h = sign | 0x7BFF
    elif exp16 <= 0:
        if exp16 < -10:
            h = sign
        else:
            mant = mant32 | 0x800000
            shift = 14 - exp16
            round_bit = 1 << (shift - 1)
            h = sign | ((mant + round_bit) >> shift)
    else:
        mant_rounded = mant32 + 0x1000
        if mant_rounded & 0x800000:
            mant_rounded = 0
            exp16 += 1
        if exp16 >= 0x1F:
            h = sign | 0x7BFF
        else:
            h = sign | (exp16 << 10) | (mant_rounded >> 13)

    return bytes(((h >> 8) & 0xFF, h & 0xFF))


def decode_float16(data) -> tuple:
    """
    Decode 2-byte big-endian IEEE 754 binary16 (or 16-bit int) into
    (speed_mps: float, is_reverse: bool), where speed_mps >= 0.0 (or NaN).
    """
    if isinstance(data, int):
        h = data & 0xFFFF
    else:
        if len(data) < 2:
            raise ValueError("float16 requires 2 bytes")
        h = (data[0] << 8) | data[1]

    is_reverse = bool(h & 0x8000)
    exp = (h >> 10) & 0x1F
    mant = h & 0x03FF

    if exp == 0:
        if mant == 0:
            return (0.0, is_reverse)
        return (math.ldexp(mant, -24), is_reverse)
    if exp == 0x1F:
        if mant == 0:
            return (float("inf"), is_reverse)
        return (float("nan"), False)
    return (math.ldexp(1024 + mant, exp - 25), is_reverse)


class ConsistListener:
    """Represents a slave locomotive attached to a consist leader's listener list."""
    __slots__ = ("node_id", "flags", "alias")

    def __init__(self, node_id, flags: int = 0, alias: int = None):
        self.node_id = NodeID(node_id)
        self.flags = flags & 0xFF
        self.alias = alias


class TractionService:
    """
    OpenLCB Traction Control Protocol Service (NMRA S-9.7.4.4).
    Can act as a Train Node responder, an LCC Throttle controller client, or both.
    """
    __slots__ = (
        "node_id",
        "alias_manager",
        "transport",
        "is_train",
        "set_speed",
        "is_reverse",
        "emergency_stop",
        "commanded_speed",
        "actual_speed",
        "functions",
        "active_controller",
        "reserved_by",
        "listeners",
        "on_speed_change",
        "on_function_change",
        "on_emergency_stop",
        "on_controller_change",
        "_rx_buffers",
        "_pending_replies",
        "_alias_cache",
    )

    def __init__(self, node_id, alias_manager, transport, is_train: bool = False):
        self.node_id = NodeID(node_id)
        self.alias_manager = alias_manager
        self.transport = transport
        self.is_train = bool(is_train)

        # Train state
        self.set_speed = 0.0
        self.is_reverse = False
        self.emergency_stop = False
        self.commanded_speed = float("nan")
        self.actual_speed = float("nan")
        self.functions = {}  # fn_address (int) -> value (int)
        self.active_controller = None
        self.reserved_by = None
        self.listeners = []  # [ConsistListener]

        # User / hardware callbacks
        self.on_speed_change = None
        self.on_function_change = None
        self.on_emergency_stop = None
        self.on_controller_change = None

        # Multi-frame reassembly {(src_alias, mti): bytearray}
        self._rx_buffers = {}
        # Pending throttle query futures {(dest_alias, cmd, subcmd): {"event": Event, "data": None}}
        self._pending_replies = {}
        # Optional NodeID bytes -> alias cache for consist listener forwarding
        self._alias_cache = {}

    @property
    def source_alias(self) -> int:
        return self.alias_manager.alias

    def enable_train(
        self,
        is_train: bool = True,
        on_speed_change=None,
        on_function_change=None,
        on_emergency_stop=None,
        on_controller_change=None,
    ):
        """Enable or configure Train Node responder behavior."""
        self.is_train = bool(is_train)
        if on_speed_change is not None:
            self.on_speed_change = on_speed_change
        if on_function_change is not None:
            self.on_function_change = on_function_change
        if on_emergency_stop is not None:
            self.on_emergency_stop = on_emergency_stop
        if on_controller_change is not None:
            self.on_controller_change = on_controller_change

    def register_node_alias(self, node_id, alias: int):
        """Cache a remote NodeID-to-alias mapping (e.g. from AMD frames) for consist forwarding."""
        self._alias_cache[NodeID(node_id).to_bytes()] = alias

    # --- Low-level Multi-Frame Addressed CAN Transport ---

    async def _send_addressed(self, mti: int, dest_alias: int, payload: bytes):
        """Send an addressed OpenLCB message, automatically fragmenting >6-byte payloads."""
        src = self.source_alias
        total = len(payload)
        if total <= 6:
            frame = CanFrame.make_addressed_message(mti, src, dest_alias, payload)
            await self.transport.send_frame(frame)
            return

        can_id = (
            0x19000000
            | (((mti | 0x008) & 0x0FFF) << 12)
            | (src & 0x0FFF)
        )
        dst_hi = (dest_alias >> 8) & 0x0F
        dst_lo = dest_alias & 0xFF

        offset = 0
        while offset < total:
            end = min(offset + 6, total)
            if offset == 0:
                flag = 0x10  # First frame
            elif end == total:
                flag = 0x20  # Final frame
            else:
                flag = 0x30  # Middle frame
            frame_data = bytes((flag | dst_hi, dst_lo)) + payload[offset:end]
            await self.transport.send_frame(CanFrame(can_id, frame_data))
            offset = end

    # --- Frame Processing & Reassembly ---

    async def process_frame(self, frame: CanFrame):
        """Handle incoming Traction Control Command (0x05EB) and Reply (0x01E9) frames."""
        if frame.is_amd and len(frame.data) == 6:
            self._alias_cache[bytes(frame.data)] = frame.source_alias
            return

        if not frame.is_openlcb_message:
            return

        mti = frame.mti
        if mti not in (MTI_TRACTION_CONTROL_COMMAND, MTI_TRACTION_CONTROL_REPLY):
            return

        if not frame.is_addressed or frame.destination_alias != self.source_alias:
            return

        if len(frame.data) < 2:
            return

        flag = (frame.data[0] >> 4) & 0x03
        chunk = frame.data[2:]
        src = frame.source_alias
        key = (src, mti)

        if flag == 0:
            payload = chunk
        elif flag == 1:
            self._rx_buffers[key] = bytearray(chunk)
            return
        elif flag == 3:
            buf = self._rx_buffers.get(key)
            if buf is None:
                return
            buf.extend(chunk)
            return
        else:  # flag == 2 (Final frame)
            buf = self._rx_buffers.pop(key, None)
            if buf is None:
                return
            buf.extend(chunk)
            payload = bytes(buf)

        if not payload:
            return

        if mti == MTI_TRACTION_CONTROL_COMMAND:
            if self.is_train:
                await self._handle_command(src, payload)
        else:
            self._handle_reply(src, payload)

    # --- Train Node Command Handlers ---

    async def _invoke(self, cb, *args):
        if cb is None:
            return
        try:
            res = cb(*args)
            if hasattr(res, "__await__"):
                await res
        except Exception:
            pass

    async def _handle_command(self, src_alias: int, payload: bytes):
        raw_cmd = payload[0]
        is_listener_cmd = bool(raw_cmd & TRACTION_LISTENER_BIT)
        cmd = raw_cmd & 0x7F

        if cmd == TRACTION_SET_SPEED and len(payload) >= 3:
            speed, reverse = decode_float16(payload[1:3])
            self.set_speed = speed
            self.is_reverse = reverse
            self.emergency_stop = False
            self.commanded_speed = speed
            await self._invoke(self.on_speed_change, speed, reverse)
            if not is_listener_cmd and self.listeners:
                await self._forward_speed_to_listeners(speed, reverse)

        elif cmd == TRACTION_SET_FUNCTION and len(payload) >= 6:
            fn_addr = (payload[1] << 16) | (payload[2] << 8) | payload[3]
            val = (payload[4] << 8) | payload[5]
            self.functions[fn_addr] = val
            await self._invoke(self.on_function_change, fn_addr, val)
            if not is_listener_cmd and self.listeners:
                await self._forward_function_to_listeners(fn_addr, val)

        elif cmd == TRACTION_EMERGENCY_STOP:
            self.emergency_stop = True
            self.set_speed = 0.0
            self.commanded_speed = 0.0
            await self._invoke(self.on_emergency_stop)
            if not is_listener_cmd and self.listeners:
                await self._forward_estop_to_listeners()

        elif cmd == TRACTION_QUERY_SPEED:
            status = 0x01 if self.emergency_stop else 0x00
            reply = bytearray((TRACTION_QUERY_SPEED,))
            reply.extend(encode_float16(self.set_speed, self.is_reverse))
            reply.append(status)
            reply.extend(encode_float16(self.commanded_speed, self.is_reverse))
            reply.extend(encode_float16(self.actual_speed, self.is_reverse))
            await self._send_addressed(MTI_TRACTION_CONTROL_REPLY, src_alias, bytes(reply))

        elif cmd == TRACTION_QUERY_FUNCTION and len(payload) >= 4:
            fn_addr = (payload[1] << 16) | (payload[2] << 8) | payload[3]
            val = self.functions.get(fn_addr, 0)
            reply = bytes((
                TRACTION_QUERY_FUNCTION,
                payload[1],
                payload[2],
                payload[3],
                (val >> 8) & 0xFF,
                val & 0xFF,
            ))
            await self._send_addressed(MTI_TRACTION_CONTROL_REPLY, src_alias, reply)

        elif cmd == TRACTION_CONTROLLER_CONFIG and len(payload) >= 2:
            await self._handle_controller_config(src_alias, payload)

        elif cmd == TRACTION_LISTENER_CONFIG and len(payload) >= 2:
            await self._handle_listener_config(src_alias, payload)

        elif cmd == TRACTION_MGMT and len(payload) >= 2:
            await self._handle_mgmt(src_alias, payload)

    async def _handle_controller_config(self, src_alias: int, payload: bytes):
        sub = payload[1]
        if sub == TRACTION_CTRL_ASSIGN and len(payload) >= 9:
            new_ctrl = NodeID(payload[3:9])
            if self.active_controller is not None and self.active_controller != new_ctrl:
                # Notify existing controller if cached, or accept transfer unless rejected
                result = 0x00
            else:
                result = 0x00
            if result == 0x00:
                self.active_controller = new_ctrl
                self._alias_cache[new_ctrl.to_bytes()] = src_alias
                await self._invoke(self.on_controller_change, new_ctrl)
            await self._send_addressed(
                MTI_TRACTION_CONTROL_REPLY,
                src_alias,
                bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_ASSIGN, result)),
            )

        elif sub == TRACTION_CTRL_RELEASE and len(payload) >= 9:
            rel_ctrl = NodeID(payload[3:9])
            if self.active_controller is None or self.active_controller == rel_ctrl:
                self.active_controller = None
                await self._invoke(self.on_controller_change, None)

        elif sub == TRACTION_CTRL_QUERY:
            if self.active_controller is not None:
                reply = bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_QUERY, 0x00)) + self.active_controller.to_bytes()
            else:
                reply = bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_QUERY, 0x01)) + (b"\x00" * 6)
            await self._send_addressed(MTI_TRACTION_CONTROL_REPLY, src_alias, reply)

        elif sub == TRACTION_CTRL_NOTIFY and len(payload) >= 9:
            # Acknowledge controller changed notification cleanly
            await self._send_addressed(
                MTI_TRACTION_CONTROL_REPLY,
                src_alias,
                bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_NOTIFY, 0x00)),
            )

    async def _handle_listener_config(self, src_alias: int, payload: bytes):
        sub = payload[1]
        if sub == TRACTION_LISTENER_ATTACH and len(payload) >= 9:
            flags = payload[2]
            nid = NodeID(payload[3:9])
            updated = False
            for lst in self.listeners:
                if lst.node_id == nid:
                    lst.flags = flags
                    updated = True
                    break
            if not updated:
                self.listeners.append(ConsistListener(nid, flags=flags, alias=self._alias_cache.get(nid.to_bytes())))
            reply = bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_ATTACH)) + nid.to_bytes() + b"\x00\x00"
            await self._send_addressed(MTI_TRACTION_CONTROL_REPLY, src_alias, reply)

        elif sub == TRACTION_LISTENER_DETACH and len(payload) >= 9:
            nid = NodeID(payload[3:9])
            before = len(self.listeners)
            self.listeners = [lst for lst in self.listeners if lst.node_id != nid]
            err = b"\x00\x00" if len(self.listeners) < before else b"\x10\x00"
            reply = bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_DETACH)) + nid.to_bytes() + err
            await self._send_addressed(MTI_TRACTION_CONTROL_REPLY, src_alias, reply)

        elif sub == TRACTION_LISTENER_QUERY:
            total = len(self.listeners)
            if len(payload) < 3:
                reply = bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_QUERY, total & 0xFF))
            else:
                idx = payload[2]
                if idx < total:
                    lst = self.listeners[idx]
                    reply = (
                        bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_QUERY, total & 0xFF, idx, lst.flags))
                        + lst.node_id.to_bytes()
                    )
                else:
                    reply = bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_QUERY, total & 0xFF, idx))
            await self._send_addressed(MTI_TRACTION_CONTROL_REPLY, src_alias, reply)

    async def _handle_mgmt(self, src_alias: int, payload: bytes):
        sub = payload[1]
        if sub == TRACTION_MGMT_RESERVE:
            if self.reserved_by is None or self.reserved_by == src_alias:
                self.reserved_by = src_alias
                result = 0x00
            else:
                result = 0x01
            await self._send_addressed(
                MTI_TRACTION_CONTROL_REPLY,
                src_alias,
                bytes((TRACTION_MGMT, TRACTION_MGMT_RESERVE, result)),
            )
        elif sub == TRACTION_MGMT_RELEASE:
            if self.reserved_by == src_alias:
                self.reserved_by = None

    # --- Consist Listener Forwarding ---

    def _resolve_listener_alias(self, lst: ConsistListener):
        if lst.alias:
            return lst.alias
        cached = self._alias_cache.get(lst.node_id.to_bytes())
        if cached:
            lst.alias = cached
        return lst.alias

    async def _forward_speed_to_listeners(self, speed_mps: float, reverse: bool):
        for lst in self.listeners:
            alias = self._resolve_listener_alias(lst)
            if not alias:
                continue
            eff_rev = (not reverse) if (lst.flags & LISTENER_FLAG_REVERSE) else reverse
            payload = bytes((TRACTION_SET_SPEED | TRACTION_LISTENER_BIT,)) + encode_float16(speed_mps, eff_rev)
            await self._send_addressed(MTI_TRACTION_CONTROL_COMMAND, alias, payload)

    async def _forward_function_to_listeners(self, fn_addr: int, val: int):
        for lst in self.listeners:
            is_f0 = fn_addr == 0
            if is_f0 and not (lst.flags & LISTENER_FLAG_LINK_F0):
                continue
            if not is_f0 and not (lst.flags & LISTENER_FLAG_LINK_FN):
                continue
            alias = self._resolve_listener_alias(lst)
            if not alias:
                continue
            payload = bytes((
                TRACTION_SET_FUNCTION | TRACTION_LISTENER_BIT,
                (fn_addr >> 16) & 0xFF,
                (fn_addr >> 8) & 0xFF,
                fn_addr & 0xFF,
                (val >> 8) & 0xFF,
                val & 0xFF,
            ))
            await self._send_addressed(MTI_TRACTION_CONTROL_COMMAND, alias, payload)

    async def _forward_estop_to_listeners(self):
        for lst in self.listeners:
            alias = self._resolve_listener_alias(lst)
            if not alias:
                continue
            await self._send_addressed(
                MTI_TRACTION_CONTROL_COMMAND,
                alias,
                bytes((TRACTION_EMERGENCY_STOP | TRACTION_LISTENER_BIT,)),
            )

    # --- Reply Dispatcher for Throttle Client ---

    def _handle_reply(self, src_alias: int, payload: bytes):
        cmd = payload[0]
        sub = payload[1] if cmd in (TRACTION_CONTROLLER_CONFIG, TRACTION_LISTENER_CONFIG, TRACTION_MGMT) and len(payload) >= 2 else 0
        key = (src_alias, cmd, sub)
        entry = self._pending_replies.get(key)
        if entry is not None:
            entry["data"] = payload
            entry["event"].set()

    async def _send_and_wait_reply(
        self,
        dest_alias: int,
        cmd_payload: bytes,
        reply_cmd: int,
        reply_subcmd: int = 0,
        timeout_ms: int = 1000,
    ) -> bytes:
        key = (dest_alias, reply_cmd, reply_subcmd)
        wait_ev = asyncio.Event()
        entry = {"event": wait_ev, "data": None}
        self._pending_replies[key] = entry
        try:
            await self._send_addressed(MTI_TRACTION_CONTROL_COMMAND, dest_alias, cmd_payload)
            await asyncio.wait_for(wait_ev.wait(), timeout=timeout_ms / 1000.0)
            return entry["data"]
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending_replies.pop(key, None)

    # --- Throttle / Controller Client API ---

    async def send_set_speed(self, dest_alias: int, speed_mps: float, reverse: bool = False):
        """Command a remote train's speed (in m/s) and direction."""
        payload = bytes((TRACTION_SET_SPEED,)) + encode_float16(speed_mps, reverse)
        await self._send_addressed(MTI_TRACTION_CONTROL_COMMAND, dest_alias, payload)

    async def send_set_function(self, dest_alias: int, fn_address: int, value: int):
        """Command a remote train's function (e.g. fn_address=0 for F0 headlight, value=1/0)."""
        payload = bytes((
            TRACTION_SET_FUNCTION,
            (fn_address >> 16) & 0xFF,
            (fn_address >> 8) & 0xFF,
            fn_address & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ))
        await self._send_addressed(MTI_TRACTION_CONTROL_COMMAND, dest_alias, payload)

    async def send_emergency_stop(self, dest_alias: int):
        """Send Emergency Stop to a remote train."""
        await self._send_addressed(
            MTI_TRACTION_CONTROL_COMMAND,
            dest_alias,
            bytes((TRACTION_EMERGENCY_STOP,)),
        )

    async def query_speed(self, dest_alias: int, timeout_ms: int = 1000) -> dict:
        """Query speed state from a remote train; returns dict or None on timeout."""
        data = await self._send_and_wait_reply(
            dest_alias,
            bytes((TRACTION_QUERY_SPEED,)),
            TRACTION_QUERY_SPEED,
            timeout_ms=timeout_ms,
        )
        if not data or len(data) < 8:
            return None
        set_spd, is_rev = decode_float16(data[1:3])
        status = data[3]
        cmd_spd, _ = decode_float16(data[4:6])
        act_spd, _ = decode_float16(data[6:8])
        return {
            "set_speed": set_spd,
            "is_reverse": is_rev,
            "emergency_stop": bool(status & 0x01),
            "commanded_speed": cmd_spd,
            "actual_speed": act_spd,
        }

    async def query_function(self, dest_alias: int, fn_address: int, timeout_ms: int = 1000) -> int:
        """Query a function value from a remote train; returns int or None on timeout."""
        req = bytes((
            TRACTION_QUERY_FUNCTION,
            (fn_address >> 16) & 0xFF,
            (fn_address >> 8) & 0xFF,
            fn_address & 0xFF,
        ))
        data = await self._send_and_wait_reply(
            dest_alias,
            req,
            TRACTION_QUERY_FUNCTION,
            timeout_ms=timeout_ms,
        )
        if not data or len(data) < 6:
            return None
        return (data[4] << 8) | data[5]

    async def assign_controller(self, dest_alias: int, flags: int = 0, timeout_ms: int = 1000) -> bool:
        """Assign this node as active controller of dest_alias train."""
        req = bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_ASSIGN, flags)) + self.node_id.to_bytes()
        data = await self._send_and_wait_reply(
            dest_alias,
            req,
            TRACTION_CONTROLLER_CONFIG,
            TRACTION_CTRL_ASSIGN,
            timeout_ms=timeout_ms,
        )
        return bool(data and len(data) >= 3 and data[2] == 0x00)

    async def release_controller(self, dest_alias: int, flags: int = 0):
        """Release control of dest_alias train."""
        req = bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_RELEASE, flags)) + self.node_id.to_bytes()
        await self._send_addressed(MTI_TRACTION_CONTROL_COMMAND, dest_alias, req)

    async def query_controller(self, dest_alias: int, timeout_ms: int = 1000):
        """Query active controller NodeID of dest_alias train (None if unassigned/timeout)."""
        req = bytes((TRACTION_CONTROLLER_CONFIG, TRACTION_CTRL_QUERY))
        data = await self._send_and_wait_reply(
            dest_alias,
            req,
            TRACTION_CONTROLLER_CONFIG,
            TRACTION_CTRL_QUERY,
            timeout_ms=timeout_ms,
        )
        if not data or len(data) < 9 or data[2] != 0x00:
            return None
        return NodeID(data[3:9])

    async def attach_listener(self, dest_alias: int, listener_node_id, flags: int = 0, timeout_ms: int = 1000) -> bool:
        """Attach a consist listener NodeID to dest_alias train."""
        req = bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_ATTACH, flags)) + NodeID(listener_node_id).to_bytes()
        data = await self._send_and_wait_reply(
            dest_alias,
            req,
            TRACTION_LISTENER_CONFIG,
            TRACTION_LISTENER_ATTACH,
            timeout_ms=timeout_ms,
        )
        return bool(data and len(data) >= 10 and data[8] == 0 and data[9] == 0)

    async def detach_listener(self, dest_alias: int, listener_node_id, flags: int = 0, timeout_ms: int = 1000) -> bool:
        """Detach a consist listener NodeID from dest_alias train."""
        req = bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_DETACH, flags)) + NodeID(listener_node_id).to_bytes()
        data = await self._send_and_wait_reply(
            dest_alias,
            req,
            TRACTION_LISTENER_CONFIG,
            TRACTION_LISTENER_DETACH,
            timeout_ms=timeout_ms,
        )
        return bool(data and len(data) >= 10 and data[8] == 0 and data[9] == 0)

    async def query_listeners(self, dest_alias: int, index: int = None, timeout_ms: int = 1000):
        """
        Query consist listener count (index=None) or a specific listener entry by index.
        Returns int count when index is None, or dict {"total", "index", "flags", "node_id"}.
        """
        req = (
            bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_QUERY))
            if index is None
            else bytes((TRACTION_LISTENER_CONFIG, TRACTION_LISTENER_QUERY, index & 0xFF))
        )
        data = await self._send_and_wait_reply(
            dest_alias,
            req,
            TRACTION_LISTENER_CONFIG,
            TRACTION_LISTENER_QUERY,
            timeout_ms=timeout_ms,
        )
        if not data or len(data) < 3:
            return None
        if index is None:
            return data[2]
        if len(data) < 11:
            return {"total": data[2], "index": data[3], "flags": 0, "node_id": None}
        return {
            "total": data[2],
            "index": data[3],
            "flags": data[4],
            "node_id": NodeID(data[5:11]),
        }

    async def reserve_train(self, dest_alias: int, timeout_ms: int = 1000) -> bool:
        """Reserve exclusive session lock before Assign/Attach operations."""
        data = await self._send_and_wait_reply(
            dest_alias,
            bytes((TRACTION_MGMT, TRACTION_MGMT_RESERVE)),
            TRACTION_MGMT,
            TRACTION_MGMT_RESERVE,
            timeout_ms=timeout_ms,
        )
        return bool(data and len(data) >= 3 and data[2] == 0x00)

    async def release_train(self, dest_alias: int):
        """Release traction management reservation."""
        await self._send_addressed(
            MTI_TRACTION_CONTROL_COMMAND,
            dest_alias,
            bytes((TRACTION_MGMT, TRACTION_MGMT_RELEASE)),
        )
