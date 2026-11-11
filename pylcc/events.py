"""
OpenLCB Event Transport Protocol (Producer / Consumer Model).
Compliant with NMRA Standard S-9.7.3.1.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .can import CanFrame
from .event_id import EventID
from .node_id import NodeID


# Event Message Type Indicators (MTIs)
MTI_PCER                        = 0x05B4  # Producer/Consumer Event Report (Global)
MTI_LEARN_EVENT                 = 0x0594  # Learn Event (Global)

MTI_IDENTIFY_CONSUMER           = 0x08F4  # Identify Consumer (Global, data = 8-byte EventID)
MTI_CONSUMER_RANGE_IDENTIFIED   = 0x04A4  # Consumer Range Identified (Global)
MTI_CONSUMER_IDENTIFIED_UNKNOWN = 0x04C7  # Consumer Identified - State Unknown
MTI_CONSUMER_IDENTIFIED_VALID   = 0x04C4  # Consumer Identified - State Valid (Active)
MTI_CONSUMER_IDENTIFIED_INVALID = 0x04C5  # Consumer Identified - State Invalid (Inactive)

MTI_IDENTIFY_PRODUCER           = 0x0914  # Identify Producer (Global, data = 8-byte EventID)
MTI_PRODUCER_RANGE_IDENTIFIED   = 0x0524  # Producer Range Identified (Global)
MTI_PRODUCER_IDENTIFIED_UNKNOWN = 0x0547  # Producer Identified - State Unknown
MTI_PRODUCER_IDENTIFIED_VALID   = 0x0544  # Producer Identified - State Valid (Active)
MTI_PRODUCER_IDENTIFIED_INVALID = 0x0545  # Producer Identified - State Invalid (Inactive)

MTI_IDENTIFY_EVENTS_ADDRESSED   = 0x0968  # Identify Events Addressed
MTI_IDENTIFY_EVENTS_GLOBAL      = 0x0976  # Identify Events Global

# Event State Constants
EVENT_STATE_UNKNOWN = -1
EVENT_STATE_INVALID = 0
EVENT_STATE_VALID   = 1


class EventConsumer:
    """Represents a locally registered event consumer."""
    __slots__ = ("event_id", "callback", "state")

    def __init__(self, event_id: EventID, callback=None, state: int = EVENT_STATE_VALID):
        self.event_id = event_id
        self.callback = callback
        self.state = state


class EventProducer:
    """Represents a locally registered event producer."""
    __slots__ = ("event_id", "state")

    def __init__(self, event_id: EventID, state: int = EVENT_STATE_VALID):
        self.event_id = event_id
        self.state = state


class EventManager:
    """
    Manages OpenLCB Event Transport for a node:
    - Registers local consumers (with callbacks invoked on PCER arrival).
    - Registers local producers and broadcasts PCER events across the bus.
    - Responds to Identify Consumer, Identify Producer, and Identify Events queries.
    """
    def __init__(self, node_id, alias_manager, transport):
        self.node_id = NodeID(node_id)
        self.alias_manager = alias_manager
        self.transport = transport
        self._consumers = {}  # EventID -> EventConsumer
        self._producers = {}  # EventID -> EventProducer

    @property
    def source_alias(self) -> int:
        return self.alias_manager.alias

    # --- Consumer Registration ---

    def register_consumer(self, event_id, callback=None, state: int = EVENT_STATE_VALID):
        """Register a local consumer for the specified EventID."""
        eid = EventID(event_id)
        consumer = EventConsumer(eid, callback=callback, state=state)
        self._consumers[eid] = consumer
        return consumer

    def unregister_consumer(self, event_id):
        """Unregister a local consumer."""
        eid = EventID(event_id)
        self._consumers.pop(eid, None)

    def is_consumer(self, event_id) -> bool:
        """True if this node consumes the specified EventID."""
        return EventID(event_id) in self._consumers

    def set_consumer_state(self, event_id, state: int):
        """Update consumer validity state (VALID, INVALID, or UNKNOWN)."""
        eid = EventID(event_id)
        if eid in self._consumers:
            self._consumers[eid].state = state

    # --- Producer Registration ---

    def register_producer(self, event_id, state: int = EVENT_STATE_VALID):
        """Register a local producer for the specified EventID."""
        eid = EventID(event_id)
        producer = EventProducer(eid, state=state)
        self._producers[eid] = producer
        return producer

    def unregister_producer(self, event_id):
        """Unregister a local producer."""
        eid = EventID(event_id)
        self._producers.pop(eid, None)

    def is_producer(self, event_id) -> bool:
        """True if this node produces the specified EventID."""
        return EventID(event_id) in self._producers

    def set_producer_state(self, event_id, state: int):
        """Update producer validity state (VALID, INVALID, or UNKNOWN)."""
        eid = EventID(event_id)
        if eid in self._producers:
            self._producers[eid].state = state

    # --- Event Transmission ---

    async def produce(self, event_id, state: int = None):
        """
        Broadcast a Producer/Consumer Event Report (PCER) for event_id.
        Optionally updates the producer state.
        """
        if not self.alias_manager.is_permitted:
            raise RuntimeError("Cannot produce event: Node alias is not in PERMITTED state")

        eid = EventID(event_id)
        if state is not None and eid in self._producers:
            self._producers[eid].state = state

        frame = CanFrame.make_global_message(
            mti=MTI_PCER,
            source_alias=self.source_alias,
            data=eid.to_bytes()
        )
        await self.transport.send_frame(frame)

    async def identify_consumers(self, event_id):
        """Broadcast an Identify Consumer query for event_id."""
        eid = EventID(event_id)
        frame = CanFrame.make_global_message(
            mti=MTI_IDENTIFY_CONSUMER,
            source_alias=self.source_alias,
            data=eid.to_bytes()
        )
        await self.transport.send_frame(frame)

    async def identify_producers(self, event_id):
        """Broadcast an Identify Producer query for event_id."""
        eid = EventID(event_id)
        frame = CanFrame.make_global_message(
            mti=MTI_IDENTIFY_PRODUCER,
            source_alias=self.source_alias,
            data=eid.to_bytes()
        )
        await self.transport.send_frame(frame)

    async def identify_events_global(self):
        """Broadcast an Identify Events Global query to discover all network events."""
        frame = CanFrame.make_global_message(
            mti=MTI_IDENTIFY_EVENTS_GLOBAL,
            source_alias=self.source_alias,
            data=b""
        )
        await self.transport.send_frame(frame)

    async def identify_events_addressed(self, dest_alias: int):
        """Send an Identify Events Addressed query targeted to dest_alias."""
        frame = CanFrame.make_addressed_message(
            mti=MTI_IDENTIFY_EVENTS_ADDRESSED,
            source_alias=self.source_alias,
            dest_alias=dest_alias,
            payload=b""
        )
        await self.transport.send_frame(frame)

    # --- Frame Reception & Query Responses ---

    async def process_frame(self, frame: CanFrame):
        """
        Process incoming CAN frames relevant to event transport.
        Dispatches PCER events to consumer callbacks and responds to identify queries.
        """
        if not frame.is_openlcb_message:
            return

        mti = frame.mti

        # 1. Producer/Consumer Event Report (PCER)
        if mti == MTI_PCER and len(frame.data) == 8:
            event_id = EventID(frame.data)
            consumer = self._consumers.get(event_id)
            if consumer and consumer.callback:
                try:
                    res = consumer.callback(event_id)
                    if hasattr(res, "__await__"):
                        await res
                except Exception as e:
                    # Callback errors should not crash the stack
                    pass

        # 2. Identify Consumer
        elif mti == MTI_IDENTIFY_CONSUMER and len(frame.data) == 8:
            event_id = EventID(frame.data)
            consumer = self._consumers.get(event_id)
            if consumer:
                await self._send_consumer_identified(consumer)

        # 3. Identify Producer
        elif mti == MTI_IDENTIFY_PRODUCER and len(frame.data) == 8:
            event_id = EventID(frame.data)
            producer = self._producers.get(event_id)
            if producer:
                await self._send_producer_identified(producer)

        # 4. Identify Events Global
        elif mti == MTI_IDENTIFY_EVENTS_GLOBAL:
            await self._respond_all_events()

        # 5. Identify Events Addressed
        elif mti == MTI_IDENTIFY_EVENTS_ADDRESSED:
            if frame.destination_alias == self.source_alias:
                await self._respond_all_events()

    async def _send_consumer_identified(self, consumer: EventConsumer):
        """Send Consumer Identified response with appropriate validity MTI."""
        if consumer.state == EVENT_STATE_VALID:
            resp_mti = MTI_CONSUMER_IDENTIFIED_VALID
        elif consumer.state == EVENT_STATE_INVALID:
            resp_mti = MTI_CONSUMER_IDENTIFIED_INVALID
        else:
            resp_mti = MTI_CONSUMER_IDENTIFIED_UNKNOWN

        frame = CanFrame.make_global_message(
            mti=resp_mti,
            source_alias=self.source_alias,
            data=consumer.event_id.to_bytes()
        )
        await self.transport.send_frame(frame)

    async def _send_producer_identified(self, producer: EventProducer):
        """Send Producer Identified response with appropriate validity MTI."""
        if producer.state == EVENT_STATE_VALID:
            resp_mti = MTI_PRODUCER_IDENTIFIED_VALID
        elif producer.state == EVENT_STATE_INVALID:
            resp_mti = MTI_PRODUCER_IDENTIFIED_INVALID
        else:
            resp_mti = MTI_PRODUCER_IDENTIFIED_UNKNOWN

        frame = CanFrame.make_global_message(
            mti=resp_mti,
            source_alias=self.source_alias,
            data=producer.event_id.to_bytes()
        )
        await self.transport.send_frame(frame)

    async def _respond_all_events(self):
        """Enumerate all registered consumers and producers in response to Identify Events."""
        for consumer in self._consumers.values():
            await self._send_consumer_identified(consumer)
        for producer in self._producers.values():
            await self._send_producer_identified(producer)
