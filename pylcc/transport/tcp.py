"""
GridConnect TCP Transport (Client and Hub/Server).
Enables OpenLCB communication over TCP with JMRI, LCC Hubs, or between microcontrollers.
Compatible with standard Python asyncio and MicroPython.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .base import BaseTransport
from ..can import CanFrame
from ..gridconnect import GridConnectParser, encode_gridconnect


class GridConnectTcpClient(BaseTransport):
    """
    Connects to a remote GridConnect TCP server (e.g., JMRI OpenLCB server or LCC WiFi Hub).
    """
    def __init__(self, host: str, port: int):
        super().__init__()
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self._rx_task = None
        self._parser = GridConnectParser()

    @property
    def is_connected(self) -> bool:
        """True if the TCP socket is currently open and connected."""
        return self.writer is not None and not self.is_closed

    async def connect(self):
        """Open TCP socket connection and start background receiver loop."""
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self._is_closed = False
        self._rx_task = asyncio.create_task(self._rx_loop())

    async def _rx_loop(self):
        """Background task that reads stream chunks from TCP socket and feeds parser."""
        try:
            while not self._is_closed:
                chunk = await self.reader.read(128)
                if not chunk:
                    # Connection closed by remote peer
                    break
                frames = self._parser.feed(chunk)
                for frame in frames:
                    self._dispatch_frame(frame)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Socket error
            pass
        finally:
            await self.close()

    async def send_frame(self, frame: CanFrame):
        """Encode CanFrame as GridConnect ASCII and send over TCP socket."""
        if self._is_closed or self.writer is None:
            raise RuntimeError("Cannot send frame: TCP client is not connected")
        text = encode_gridconnect(frame) + "\r\n"
        self.writer.write(text.encode("latin1"))
        await self.writer.drain()

    async def close(self):
        """Close TCP socket and cancel background tasks."""
        if self._is_closed:
            return
        await super().close()

        if self._rx_task and not self._rx_task.done():
            self._rx_task.cancel()
            try:
                await self._rx_task
            except (asyncio.CancelledError, Exception):
                pass

        if self.writer is not None:
            try:
                self.writer.close()
                if hasattr(self.writer, "wait_closed"):
                    await self.writer.wait_closed()
            except Exception:
                pass
            self.writer = None
            self.reader = None


class GridConnectTcpServer(BaseTransport):
    """
    Runs a lightweight GridConnect TCP Server (Hub).
    Allows external nodes (or JMRI) to connect to this node.
    Any frame sent by a client is broadcast to all other clients and delivered to local node.
    """
    def __init__(self, host: str = "0.0.0.0", port: int = 12021):
        super().__init__()
        self.host = host
        self.port = port
        self.server = None
        self._clients = set()
        self._client_parsers = {}

    @property
    def is_connected(self) -> bool:
        """True if the TCP server is currently running."""
        return self.server is not None and not self.is_closed

    async def connect(self):
        """Alias for start() to provide unified BaseTransport lifecycle."""
        await self.start()

    async def start(self):
        """Start listening for incoming TCP GridConnect connections."""
        self._is_closed = False
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def _handle_client(self, reader, writer):
        """Handle an individual connected TCP client."""
        client_key = (reader, writer)
        self._clients.add(client_key)
        parser = GridConnectParser()
        self._client_parsers[client_key] = parser

        try:
            while not self._is_closed:
                chunk = await reader.read(128)
                if not chunk:
                    break
                frames = parser.feed(chunk)
                for frame in frames:
                    # Deliver to local node
                    self._dispatch_frame(frame)
                    # Forward to all other connected clients
                    await self._broadcast_to_clients(frame, exclude_writer=writer)
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            self._clients.discard(client_key)
            self._client_parsers.pop(client_key, None)
            try:
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
            except Exception:
                pass

    async def _broadcast_to_clients(self, frame: CanFrame, exclude_writer=None):
        """Send GridConnect frame to connected clients."""
        text = encode_gridconnect(frame) + "\r\n"
        data = text.encode("latin1")
        dead_clients = []
        for reader, writer in list(self._clients):
            if writer is not exclude_writer:
                try:
                    writer.write(data)
                    await writer.drain()
                except Exception:
                    dead_clients.append((reader, writer))

        for c in dead_clients:
            self._clients.discard(c)
            self._client_parsers.pop(c, None)

    async def send_frame(self, frame: CanFrame):
        """Send a frame from the local node to all connected TCP clients."""
        if self._is_closed:
            raise RuntimeError("Cannot send frame: TCP server is closed")
        await self._broadcast_to_clients(frame)

    async def close(self):
        """Close server and disconnect all clients."""
        if self._is_closed:
            return
        await super().close()

        for reader, writer in list(self._clients):
            try:
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
        self._client_parsers.clear()

        if self.server is not None:
            self.server.close()
            if hasattr(self.server, "wait_closed"):
                await self.server.wait_closed()
            self.server = None
