"""
Native ESP32 / ESP32-C6 TWAI (Two-Wire Automotive Interface / CAN 2.0B) Transport.
Supports MicroPython's machine.TWAI (and machine.CAN fallback).
Configured for OpenLCB standard 125 kbps bitrate with 29-bit extended frames.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from ..can import CanFrame
from ..transport.base import BaseTransport

if hasattr(asyncio, "sleep_ms"):
    _sleep_1ms = lambda: asyncio.sleep_ms(1)
else:
    _sleep_1ms = lambda: asyncio.sleep(0.001)

# Attempt MicroPython machine imports
try:
    from machine import Pin, TWAI
except (ImportError, AttributeError):
    try:
        from machine import Pin, CAN as TWAI
    except (ImportError, AttributeError):
        TWAI = None
        Pin = None


class Esp32TwaiTransport(BaseTransport):
    """
    Hardware CAN transport for ESP32 and ESP32-C6 using the on-chip TWAI peripheral.
    Communicates at standard OpenLCB 125 kbps using 29-bit extended frames.
    """
    def __init__(
        self,
        tx_pin: int = 5,
        rx_pin: int = 4,
        baudrate: int = 125000,
        twai_id: int = 0,
        twai_obj=None,
    ):
        super().__init__()
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.baudrate = baudrate
        self.twai_id = twai_id
        self._twai = twai_obj
        self._rx_task = None
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected and not self.is_closed

    async def connect(self):
        """Initialize TWAI peripheral and launch non-blocking background frame reader."""
        if self._is_connected:
            return

        if self._twai is None:
            if TWAI is None:
                raise RuntimeError(
                    "ESP32 TWAI peripheral not available. Ensure you are running on an ESP32 "
                    "MicroPython build with TWAI/CAN support."
                )

            # Initialize TWAI peripheral at 125 kbps in normal mode
            # OpenLCB requires receiving all 29-bit frames across the bus
            tx = Pin(self.tx_pin) if isinstance(self.tx_pin, int) else self.tx_pin
            rx = Pin(self.rx_pin) if isinstance(self.rx_pin, int) else self.rx_pin

            try:
                # Standard machine.TWAI signature
                self._twai = TWAI(
                    self.twai_id,
                    mode=getattr(TWAI, "NORMAL", 0),
                    baudrate=self.baudrate,
                    tx=tx,
                    rx=rx,
                )
            except TypeError:
                # Fallback for builds with alternate parameter names (e.g. extframe or pins tuple)
                try:
                    self._twai = TWAI(
                        self.twai_id,
                        getattr(TWAI, "NORMAL", 0),
                        baudrate=self.baudrate,
                        tx=tx,
                        rx=rx,
                    )
                except Exception:
                    self._twai = TWAI(
                        self.twai_id,
                        baudrate=self.baudrate,
                        tx=tx,
                        rx=rx,
                    )

        self._is_connected = True
        self._is_closed = False
        self._rx_task = asyncio.create_task(self._rx_loop())

    async def _rx_loop(self):
        """Asynchronous background worker polling TWAI FIFO and dispatching frames."""
        has_any = hasattr(self._twai, "any")
        while self._is_connected and not self.is_closed:
            try:
                # Drain up to 8 pending frames per tick without starving event loop
                for _ in range(8):
                    if has_any and not self._twai.any():
                        break
                    msg = self._twai.recv()
                    if msg is None:
                        break
                    frame = self._convert_msg_to_frame(msg)
                    if frame is not None:
                        self._dispatch_frame(frame)
            except Exception:
                # Buffer empty or transient peripheral read condition
                pass

            # Yield to other asyncio tasks
            await _sleep_1ms()

    def _convert_msg_to_frame(self, msg) -> CanFrame:
        """
        Convert native TWAI tuple to pyLCC CanFrame:
        Supports both (can_id, is_extended, is_rtr, data) and (data, can_id, is_ext, is_rtr).
        """
        if isinstance(msg, CanFrame):
            return msg

        if isinstance(msg, (tuple, list)):
            if len(msg) >= 4:
                # Standard MicroPython TWAI format: (id, is_extended, is_rtr, data)
                if isinstance(msg[0], int):
                    can_id = msg[0]
                    is_extended = bool(msg[1])
                    is_rtr = bool(msg[2])
                    data = msg[3] or b""
                # Alternate format: (data, id, is_extended, is_rtr)
                elif isinstance(msg[0], (bytes, bytearray)):
                    data = msg[0]
                    can_id = msg[1]
                    is_extended = bool(msg[2])
                    is_rtr = bool(msg[3])
                else:
                    return None

                return CanFrame(
                    can_id=can_id,
                    data=data,
                    is_extended=is_extended,
                    is_rtr=is_rtr,
                )

        return None

    async def send_frame(self, frame: CanFrame) -> bool:
        """Transmit a 29-bit CAN frame via ESP32 TWAI peripheral."""
        if not self._is_connected or self._twai is None:
            raise RuntimeError("Cannot send frame: ESP32 TWAI transport is not connected")

        can_id = frame.can_id
        data = frame.data
        is_extended = frame.is_extended
        is_rtr = frame.is_rtr

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # MicroPython TWAI.send(id, data, is_ext=True, is_rtr=False)
                # or send(id, data, extframe=True)
                if hasattr(self._twai, "send"):
                    try:
                        self._twai.send(can_id, data, is_ext=is_extended, is_rtr=is_rtr)
                    except TypeError:
                        try:
                            self._twai.send(can_id, data, extframe=is_extended, rtr=is_rtr)
                        except TypeError:
                            self._twai.send(can_id, data)
                    return True
            except (OSError, Exception):
                # TX FIFO full; yield and retry
                if attempt < max_attempts - 1:
                    await _sleep_1ms()

        return False

    async def close(self):
        """Shut down the background RX task and deinitialize TWAI peripheral."""
        self._is_connected = False
        await super().close()

        if self._rx_task is not None:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except (asyncio.CancelledError, Exception):
                pass
            self._rx_task = None

        if self._twai is not None:
            try:
                if hasattr(self._twai, "deinit"):
                    self._twai.deinit()
            except Exception:
                pass
