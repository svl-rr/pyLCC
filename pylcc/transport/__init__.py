"""
pyLCC Transport Layer Abstraction.
Supports GridConnect over TCP, USB/UART Serial, in-memory loopback, and hardware CAN interfaces.
"""

from .base import BaseTransport
from .loopback import LoopbackTransport
from .tcp import GridConnectTcpClient, GridConnectTcpServer
from .serial import GridConnectSerialTransport

__all__ = [
    "BaseTransport",
    "LoopbackTransport",
    "GridConnectTcpClient",
    "GridConnectTcpServer",
    "GridConnectSerialTransport",
]
