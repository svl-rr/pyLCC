"""
Base Transport Interface for OpenLCB Frames.
Compatible with standard Python asyncio and MicroPython uasyncio.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from ..can import CanFrame


class BaseTransport:
    """
    Abstract Base Class for OpenLCB frame transports.
    Provides async send/receive methods and frame listener callbacks.
    """
    def __init__(self):
        self._listeners = []
        self._rx_queue = asyncio.Queue()
        self._is_closed = False

    def add_listener(self, callback):
        """Register a callback(frame) invoked whenever a frame is received."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback):
        """Unregister a frame callback."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _dispatch_frame(self, frame: CanFrame):
        """Internal helper to dispatch a received frame to queue and listeners."""
        self._rx_queue.put_nowait(frame)
        for callback in self._listeners:
            try:
                callback(frame)
            except Exception as e:
                # Callback errors should not crash the transport loop
                pass

    async def send_frame(self, frame: CanFrame):
        """Send a CAN frame across this transport."""
        raise NotImplementedError

    async def receive_frame(self) -> CanFrame:
        """Receive the next CAN frame from this transport."""
        return await self._rx_queue.get()

    async def close(self):
        """Close the transport and release any resources."""
        self._is_closed = True

    @property
    def is_closed(self) -> bool:
        return self._is_closed
