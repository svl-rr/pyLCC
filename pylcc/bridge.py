"""
OpenLCB / LCC Bidirectional CAN <-> WiFi (GridConnect TCP) Bridge.
Bridges traffic seamlessly between physical CAN buses and wireless/networked clients (e.g. JMRI).
Runs on ESP32, ESP32-C6, Raspberry Pi Pico W + MCP2518FD, or desktop gateways.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .can import CanFrame
from .transport.base import BaseTransport


class LCCBridge:
    """
    Bidirectional OpenLCB / LCC Bridge.
    Transparently forwards frames between a physical CAN transport and a GridConnect TCP transport.
    Maintains packet statistics and error counters.
    """
    def __init__(
        self,
        can_transport: BaseTransport,
        tcp_transport: BaseTransport,
        verbose: bool = False,
    ):
        self.can_transport = can_transport
        self.tcp_transport = tcp_transport
        self.verbose = verbose

        # Bridge statistics
        self.can_to_tcp_count = 0
        self.tcp_to_can_count = 0
        self.can_tx_errors = 0
        self.tcp_tx_errors = 0

        self._running = False
        self._can_listener = self._on_can_frame_received
        self._tcp_listener = self._on_tcp_frame_received

    @property
    def is_running(self) -> bool:
        return self._running

    def _on_can_frame_received(self, frame: CanFrame):
        """Called when a physical CAN frame arrives; forwards to TCP."""
        if not self._running:
            return
        asyncio.create_task(self._forward_to_tcp(frame))

    def _on_tcp_frame_received(self, frame: CanFrame):
        """Called when a GridConnect frame arrives via TCP; forwards to CAN."""
        if not self._running:
            return
        asyncio.create_task(self._forward_to_can(frame))

    async def _forward_to_tcp(self, frame: CanFrame):
        try:
            await self.tcp_transport.send_frame(frame)
            self.can_to_tcp_count += 1
            if self.verbose:
                print(f"[CAN -> TCP] ID=0x{frame.can_id:08X} Src=0x{frame.source_alias:03X} Data={frame.data.hex()}")
        except Exception:
            self.tcp_tx_errors += 1

    async def _forward_to_can(self, frame: CanFrame):
        try:
            ok = await self.can_transport.send_frame(frame)
            if ok or ok is None:
                self.tcp_to_can_count += 1
                if self.verbose:
                    print(f"[TCP -> CAN] ID=0x{frame.can_id:08X} Src=0x{frame.source_alias:03X} Data={frame.data.hex()}")
            else:
                self.can_tx_errors += 1
        except Exception:
            self.can_tx_errors += 1

    async def start(self):
        """Connect both transports and activate bidirectional forwarding."""
        if self._running:
            return

        # Connect CAN transport if not connected
        if hasattr(self.can_transport, "connect"):
            if hasattr(self.can_transport, "is_connected"):
                if not self.can_transport.is_connected:
                    await self.can_transport.connect()
            else:
                await self.can_transport.connect()

        # Connect TCP transport if not connected
        if hasattr(self.tcp_transport, "connect"):
            if hasattr(self.tcp_transport, "is_connected"):
                if not self.tcp_transport.is_connected:
                    await self.tcp_transport.connect()
            else:
                await self.tcp_transport.connect()

        # Wire listeners
        self.can_transport.add_listener(self._can_listener)
        self.tcp_transport.add_listener(self._tcp_listener)

        self._running = True

    async def stop(self):
        """Detach listeners and stop bridging."""
        if not self._running:
            return

        self._running = False

        try:
            self.can_transport.remove_listener(self._can_listener)
        except Exception:
            pass

        try:
            self.tcp_transport.remove_listener(self._tcp_listener)
        except Exception:
            pass

        if hasattr(self.can_transport, "close"):
            await self.can_transport.close()

        if hasattr(self.tcp_transport, "close"):
            await self.tcp_transport.close()

    def get_stats(self) -> dict:
        """Return diagnostic statistics."""
        return {
            "can_to_tcp": self.can_to_tcp_count,
            "tcp_to_can": self.tcp_to_can_count,
            "can_tx_errors": self.can_tx_errors,
            "tcp_tx_errors": self.tcp_tx_errors,
        }
