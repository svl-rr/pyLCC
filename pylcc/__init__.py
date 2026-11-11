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
]
