"""
Native STM32 CAN / FDCAN Controller Transport for pyLCC.
Utilizes MicroPython's built-in pyb.CAN module for STM32 microcontrollers.
Configured for OpenLCB standard 125 kbps with 29-bit extended frames.
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

# Attempt MicroPython pyb import
try:
    import pyb
except ImportError:
    pyb = None


class Stm32CanTransport(BaseTransport):
    """
    Hardware CAN transport for STM32 microcontrollers using pyb.CAN.
    Communicates at OpenLCB 125 kbps with 29-bit extended frames.
    """
    def __init__(
        self,
        bus_id: int = 1,
        baudrate: int = 125000,
        can_obj=None,
        **can_kwargs,
    ):
        super().__init__()
        self.bus_id = bus_id
        self.baudrate = baudrate
        self.can_kwargs = can_kwargs
        self._can = can_obj
        self._rx_task = None
        self._is_connected = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected and not self.is_closed

    async def connect(self):
        """Initialize STM32 CAN peripheral, set 29-bit acceptance filter, and start RX loop."""
        if self._is_connected:
            return

        if self._can is None:
            if pyb is None or not hasattr(pyb, "CAN"):
                raise RuntimeError(
                    "STM32 pyb.CAN module not available. Stm32CanTransport requires an STM32 "
                    "MicroPython build (e.g. Pyboard) or a custom CAN object."
                )

            # Initialize pyb.CAN in NORMAL mode at 125 kbps with extended frame support
            self._can = pyb.CAN(
                self.bus_id,
                pyb.CAN.NORMAL,
                baudrate=self.baudrate,
                extframe=True,
                **self.can_kwargs,
            )

        # Configure Filter Bank 0: 32-bit mask mode, FIFO 0
        # Matches all 29-bit extended frames across the OpenLCB bus (id=0, mask=0)
        if hasattr(self._can, "setfilter"):
            mask_mode = getattr(getattr(pyb, "CAN", None), "MASK32", 0)
            self._can.setfilter(0, mask_mode, 0, (0, 0))

        self._is_connected = True
        self._is_closed = False
        self._rx_task = asyncio.create_task(self._rx_loop())

    async def _rx_loop(self):
        """Poll STM32 CAN receive FIFO 0 and dispatch incoming frames."""
        has_any = hasattr(self._can, "any")
        while self._is_connected and not self.is_closed:
            try:
                # Drain up to 8 pending frames per tick without starving event loop
                for _ in range(8):
                    if has_any and not self._can.any(0):
                        break
                    # pyb.CAN.recv(fifo) -> (id, is_extended, is_rtr, fmi, data)
                    msg = self._can.recv(0, timeout=0)
                    if msg is None:
                        break
                    frame = self._convert_msg_to_frame(msg)
                    if frame is not None:
                        self._dispatch_frame(frame)
            except Exception:
                pass

            await _sleep_1ms()

    def _convert_msg_to_frame(self, msg) -> CanFrame:
        """
        Convert native pyb.CAN tuple to pyLCC CanFrame:
        Expected pyb.CAN format: (can_id, is_extended, is_rtr, fmi, data)
        """
        if isinstance(msg, CanFrame):
            return msg

        if isinstance(msg, (tuple, list)):
            if len(msg) >= 5:
                # Standard pyb.CAN format
                can_id = msg[0]
                is_extended = bool(msg[1])
                is_rtr = bool(msg[2])
                data = msg[4] or b""
                return CanFrame(
                    can_id=can_id,
                    data=data,
                    is_extended=is_extended,
                    is_rtr=is_rtr,
                )
            elif len(msg) == 4:
                # Alternate 4-element format (can_id, is_ext, is_rtr, data)
                return CanFrame(
                    can_id=msg[0],
                    data=msg[3] or b"",
                    is_extended=bool(msg[1]),
                    is_rtr=bool(msg[2]),
                )

        return None

    async def send_frame(self, frame: CanFrame) -> bool:
        """Transmit a 29-bit CAN frame via STM32 on-chip CAN controller."""
        if not self._is_connected or self._can is None:
            raise RuntimeError("Cannot send frame: STM32 CAN transport is not connected")

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                # pyb.CAN.send(data, id, timeout=10, is_extended=True)
                if hasattr(self._can, "send"):
                    self._can.send(
                        frame.data,
                        frame.can_id,
                        timeout=10,
                        is_extended=frame.is_extended,
                    )
                    return True
            except Exception:
                if attempt < max_attempts - 1:
                    await _sleep_1ms()

        return False

    async def close(self):
        """Stop background RX task and deinitialize CAN controller."""
        self._is_connected = False
        await super().close()

        if self._rx_task is not None:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except (asyncio.CancelledError, Exception):
                pass
            self._rx_task = None

        if self._can is not None:
            try:
                if hasattr(self._can, "deinit"):
                    self._can.deinit()
            except Exception:
                pass
