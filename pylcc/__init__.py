"""
pyLCC: A lightweight MicroPython & Python OpenLCB / LCC (Layout Command Control) stack.
Designed for ESP32, Raspberry Pi Pico, STM32, and desktop Python.
"""

from .can import CanFrame
from .gridconnect import GridConnectParser, encode_gridconnect, decode_gridconnect
from .node_id import NodeID
from .event_id import EventID
from .alias import AliasManager, AliasPrng
from .events import (
    EventManager,
    EVENT_STATE_VALID,
    EVENT_STATE_INVALID,
    EVENT_STATE_UNKNOWN,
    MTI_PCER,
    MTI_IDENTIFY_CONSUMER,
    MTI_IDENTIFY_PRODUCER,
    MTI_CONSUMER_IDENTIFIED_VALID,
    MTI_PRODUCER_IDENTIFIED_VALID,
    MTI_IDENTIFY_EVENTS_GLOBAL,
    MTI_IDENTIFY_EVENTS_ADDRESSED,
)
from .datagram import (
    Datagram,
    DatagramManager,
    DATAGRAM_MAX_SIZE,
    MTI_DATAGRAM_OK,
    MTI_DATAGRAM_REJECTED,
)
from .snip import (
    SnipData,
    SnipService,
    MTI_PROTOCOL_SUPPORT_INQUIRY,
    MTI_PROTOCOL_SUPPORT_REPLY,
    MTI_SIMPLE_NODE_IDENT_INFO_REQUEST,
    MTI_SIMPLE_NODE_IDENT_INFO_REPLY,
    PIP_SIMPLE_NODE,
    PIP_DATAGRAM,
    PIP_MEMORY_CONFIG,
    PIP_EVENT_EXCHANGE,
    PIP_SNIP,
    PIP_CDI,
)

__version__ = "0.1.0"
__all__ = [
    "CanFrame",
    "GridConnectParser",
    "encode_gridconnect",
    "decode_gridconnect",
    "NodeID",
    "EventID",
    "AliasManager",
    "AliasPrng",
    "EventManager",
    "EVENT_STATE_VALID",
    "EVENT_STATE_INVALID",
    "EVENT_STATE_UNKNOWN",
    "MTI_PCER",
    "MTI_IDENTIFY_CONSUMER",
    "MTI_IDENTIFY_PRODUCER",
    "MTI_CONSUMER_IDENTIFIED_VALID",
    "MTI_PRODUCER_IDENTIFIED_VALID",
    "MTI_IDENTIFY_EVENTS_GLOBAL",
    "MTI_IDENTIFY_EVENTS_ADDRESSED",
    "Datagram",
    "DatagramManager",
    "DATAGRAM_MAX_SIZE",
    "MTI_DATAGRAM_OK",
    "MTI_DATAGRAM_REJECTED",
    "SnipData",
    "SnipService",
    "MTI_PROTOCOL_SUPPORT_INQUIRY",
    "MTI_PROTOCOL_SUPPORT_REPLY",
    "MTI_SIMPLE_NODE_IDENT_INFO_REQUEST",
    "MTI_SIMPLE_NODE_IDENT_INFO_REPLY",
    "PIP_SIMPLE_NODE",
    "PIP_DATAGRAM",
    "PIP_MEMORY_CONFIG",
    "PIP_EVENT_EXCHANGE",
    "PIP_SNIP",
    "PIP_CDI",
]
