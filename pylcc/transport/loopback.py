"""
In-Memory Loopback Transport for testing pyLCC nodes without hardware or network.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .base import BaseTransport
from ..can import CanFrame


class LoopbackBus:
    """
    A simulated shared CAN/GridConnect bus that interconnects multiple LoopbackTransports.
    """
    def __init__(self):
        self._transports = []

    def attach(self, transport: "LoopbackTransport"):
        if transport not in self._transports:
            self._transports.append(transport)

    def detach(self, transport: "LoopbackTransport"):
        if transport in self._transports:
            self._transports.remove(transport)

    def broadcast(self, sender: "LoopbackTransport", frame: CanFrame):
        """Distribute frame to all attached transports except sender."""
        for transport in self._transports:
            if transport is not sender and not transport.is_closed:
                transport._dispatch_frame(frame)


class LoopbackTransport(BaseTransport):
    """
    Loopback endpoint attached to a shared LoopbackBus.
    """
    def __init__(self, bus: LoopbackBus = None):
        super().__init__()
        self.bus = bus
        if self.bus is not None:
            self.bus.attach(self)

    async def send_frame(self, frame: CanFrame):
        if self._is_closed:
            raise RuntimeError("Cannot send frame on closed transport")
        if self.bus is not None:
            self.bus.broadcast(self, frame)

    async def close(self):
        await super().close()
        if self.bus is not None:
            self.bus.detach(self)
