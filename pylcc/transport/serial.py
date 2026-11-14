"""
GridConnect USB / UART Serial Transport for pyLCC.
Enables OpenLCB communication over USB-to-CAN adapters (CANable, LCC Buffer-USB)
or direct TTL UART links on MicroPython (machine.UART) and desktop Python (pyserial).
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .base import BaseTransport
from ..can import CanFrame
from ..gridconnect import GridConnectParser, encode_gridconnect

_SLEEP_MS = getattr(asyncio, "sleep_ms", None)


async def _sleep(ms: int):
    if _SLEEP_MS is not None:
        await _SLEEP_MS(ms)
    else:
        await asyncio.sleep(ms * 0.001)


class GridConnectSerialTransport(BaseTransport):
    """
    Asynchronous GridConnect transport over USB CDC serial or hardware UART.

    On MicroPython, pass an integer UART ID (with optional ``tx=``, ``rx=`` pins)
    or an already-initialized ``machine.UART`` / stream via ``uart=``.
    On desktop Python, pass a serial device path (e.g. ``"/dev/ttyUSB0"`` or
    ``"COM3"``) backed by ``pyserial``, or an injected stream via ``uart=``.
    """
    __slots__ = (
        "port",
        "baudrate",
        "uart_kwargs",
        "poll_interval_ms",
        "close_uart_on_exit",
        "_uart",
        "_owns_uart",
        "_is_connected",
        "_parser",
        "_rx_task",
    )

    def __init__(
        self,
        port=0,
        baudrate: int = 115200,
        uart=None,
        poll_interval_ms: int = 5,
        close_uart_on_exit: bool = True,
        **uart_kwargs,
    ):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.uart_kwargs = uart_kwargs
        self.poll_interval_ms = poll_interval_ms
        self.close_uart_on_exit = close_uart_on_exit
        self._uart = uart
        self._owns_uart = False
        self._is_connected = False
        self._parser = GridConnectParser()
        self._rx_task = None

    @property
    def is_connected(self) -> bool:
        """True if connect() has succeeded and transport is not closed."""
        return self._is_connected and self._uart is not None and not self._is_closed

    async def connect(self):
        """Open the UART or serial device (if not injected) and start the RX loop."""
        if self._uart is None:
            opened = None
            if isinstance(self.port, int):
                try:
                    import machine
                    opened = machine.UART(self.port, baudrate=self.baudrate, **self.uart_kwargs)
                except (ImportError, AttributeError):
                    opened = None
            if opened is None:
                try:
                    import serial
                except ImportError as exc:
                    raise RuntimeError(
                        "Unable to open serial port: neither machine.UART nor pyserial is available; "
                        "pass an initialized uart/stream object via uart=..."
                    ) from exc
                kwargs = {"timeout": 0}
                kwargs.update(self.uart_kwargs)
                opened = serial.Serial(self.port, baudrate=self.baudrate, **kwargs)
            self._uart = opened
            self._owns_uart = True

        self._is_closed = False
        self._is_connected = True
        if self._rx_task is None or self._rx_task.done():
            self._rx_task = asyncio.create_task(self._rx_loop())

    def poll(self) -> int:
        """
        Non-blocking read of pending serial bytes, parsing and dispatching complete frames.
        Returns the number of CAN frames dispatched.
        """
        if self._is_closed or self._uart is None:
            return 0

        available = None
        if hasattr(self._uart, "any"):
            available = self._uart.any()
            if available <= 0:
                return 0
        elif hasattr(self._uart, "in_waiting"):
            available = self._uart.in_waiting
            if available <= 0:
                return 0

        chunk = self._uart.read(available) if available else self._uart.read(256)
        if not chunk:
            return 0
        if isinstance(chunk, str):
            chunk = chunk.encode("latin1")

        frames = self._parser.feed(chunk)
        for frame in frames:
            self._dispatch_frame(frame)
        return len(frames)

    async def _rx_loop(self):
        """Background coroutine polling the serial interface for incoming GridConnect frames."""
        try:
            while not self._is_closed:
                dispatched = self.poll()
                if dispatched == 0:
                    await _sleep(self.poll_interval_ms)
                else:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass
        except Exception:
            await self.close()

    async def send_frame(self, frame: CanFrame):
        """Encode CanFrame as GridConnect ASCII and write to serial port / UART."""
        if not self.is_connected:
            raise RuntimeError("Cannot send frame: Serial transport is not connected")

        raw = (encode_gridconnect(frame) + "\r\n").encode("latin1")
        written = self._uart.write(raw)
        if isinstance(written, int) and 0 <= written < len(raw):
            view = memoryview(raw)
            offset = written
            while offset < len(raw):
                n = self._uart.write(view[offset:])
                if not n:
                    await _sleep(1)
                else:
                    offset += n

    async def close(self):
        """Stop background RX task and close the serial port / UART if owned."""
        if self._is_closed and not self._is_connected:
            return
        self._is_connected = False
        await super().close()

        if self._rx_task and not self._rx_task.done():
            self._rx_task.cancel()
            try:
                await self._rx_task
            except (asyncio.CancelledError, Exception):
                pass
        self._rx_task = None

        if self._uart is not None and (self._owns_uart or self.close_uart_on_exit):
            try:
                if hasattr(self._uart, "close"):
                    self._uart.close()
                elif self._owns_uart and hasattr(self._uart, "deinit"):
                    self._uart.deinit()
            except Exception:
                pass
            if self._owns_uart:
                self._uart = None
