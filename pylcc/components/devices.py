"""
Layout component implementations and debug stubs for pyLCC.
Includes Turnout, Sensor / BlockDetector, Signal / SignalMast, and Output / Light.
Designed for easy integration with existing user hardware drivers.
"""

from ..event_id import EventID
from ..events import EVENT_STATE_VALID, EVENT_STATE_INVALID, EVENT_STATE_UNKNOWN
from .base import BaseComponent


# --- Turnout Component ---

class Turnout(BaseComponent):
    """
    OpenLCB Turnout (Switch Machine).
    Consumes Close and Throw command events.
    Optionally produces Is Closed and Is Thrown feedback events.
    """
    STATE_CLOSED = "CLOSED"
    STATE_THROWN = "THROWN"
    STATE_UNKNOWN = "UNKNOWN"

    def __init__(
        self,
        name: str = "",
        close_event=None,
        throw_event=None,
        is_closed_event=None,
        is_thrown_event=None,
        initial_state: str = STATE_CLOSED,
        on_change=None,
    ):
        super().__init__(name=name, on_change=on_change)
        self.close_event = EventID(close_event) if close_event else None
        self.throw_event = EventID(throw_event) if throw_event else None
        self.is_closed_event = EventID(is_closed_event) if is_closed_event else None
        self.is_thrown_event = EventID(is_thrown_event) if is_thrown_event else None
        self.state = initial_state

    def setup_events(self):
        if not self.node:
            return

        # Register consumers for commands
        if self.close_event:
            self.node.event_manager.register_consumer(
                self.close_event,
                self._handle_close_event,
                state=EVENT_STATE_VALID if self.state == self.STATE_CLOSED else EVENT_STATE_INVALID,
            )
        if self.throw_event:
            self.node.event_manager.register_consumer(
                self.throw_event,
                self._handle_throw_event,
                state=EVENT_STATE_VALID if self.state == self.STATE_THROWN else EVENT_STATE_INVALID,
            )

        # Register producers for feedback
        if self.is_closed_event:
            self.node.event_manager.register_producer(
                self.is_closed_event,
                state=EVENT_STATE_VALID if self.state == self.STATE_CLOSED else EVENT_STATE_INVALID,
            )
        if self.is_thrown_event:
            self.node.event_manager.register_producer(
                self.is_thrown_event,
                state=EVENT_STATE_VALID if self.state == self.STATE_THROWN else EVENT_STATE_INVALID,
            )

    async def _handle_close_event(self, event_id: EventID):
        await self.set_state(self.STATE_CLOSED, broadcast=True)

    async def _handle_throw_event(self, event_id: EventID):
        await self.set_state(self.STATE_THROWN, broadcast=True)

    async def close(self):
        """Command turnout to CLOSE."""
        await self.set_state(self.STATE_CLOSED, broadcast=True)

    async def throw(self):
        """Command turnout to THROW."""
        await self.set_state(self.STATE_THROWN, broadcast=True)

    async def set_state(self, state: str, broadcast: bool = True):
        """Update turnout state, notify callbacks, and optionally produce feedback events."""
        if state not in (self.STATE_CLOSED, self.STATE_THROWN, self.STATE_UNKNOWN):
            raise ValueError(f"Invalid turnout state: {state}")

        prev_state = self.state
        self.state = state

        # Update event states in EventManager
        if self.node and self.node.event_manager:
            if self.close_event:
                self.node.event_manager.set_consumer_state(
                    self.close_event,
                    EVENT_STATE_VALID if state == self.STATE_CLOSED else EVENT_STATE_INVALID,
                )
            if self.throw_event:
                self.node.event_manager.set_consumer_state(
                    self.throw_event,
                    EVENT_STATE_VALID if state == self.STATE_THROWN else EVENT_STATE_INVALID,
                )
            if self.is_closed_event:
                self.node.event_manager.set_producer_state(
                    self.is_closed_event,
                    EVENT_STATE_VALID if state == self.STATE_CLOSED else EVENT_STATE_INVALID,
                )
            if self.is_thrown_event:
                self.node.event_manager.set_producer_state(
                    self.is_thrown_event,
                    EVENT_STATE_VALID if state == self.STATE_THROWN else EVENT_STATE_INVALID,
                )

            # Produce feedback PCER if state changed and broadcast requested
            if broadcast and state != prev_state:
                if state == self.STATE_CLOSED and self.is_closed_event:
                    await self.node.produce_event(self.is_closed_event)
                elif state == self.STATE_THROWN and self.is_thrown_event:
                    await self.node.produce_event(self.is_thrown_event)

        # Notify user hardware callback
        if state != prev_state:
            await self._notify_change(state)


# --- Sensor / BlockDetector / Input Component ---

class Sensor(BaseComponent):
    """
    OpenLCB Sensor / Block Occupancy Detector / Push Button.
    Produces Active (Occupied / Pressed) and Inactive (Cleared / Released) events.
    """
    STATE_INACTIVE = "INACTIVE"
    STATE_ACTIVE = "ACTIVE"

    def __init__(
        self,
        name: str = "",
        active_event=None,
        inactive_event=None,
        initial_state: str = STATE_INACTIVE,
        on_change=None,
    ):
        super().__init__(name=name, on_change=on_change)
        self.active_event = EventID(active_event) if active_event else None
        self.inactive_event = EventID(inactive_event) if inactive_event else None
        self.state = initial_state

    def setup_events(self):
        if not self.node:
            return

        is_active = self.state == self.STATE_ACTIVE
        if self.active_event:
            self.node.event_manager.register_producer(
                self.active_event,
                state=EVENT_STATE_VALID if is_active else EVENT_STATE_INVALID,
            )
        if self.inactive_event:
            self.node.event_manager.register_producer(
                self.inactive_event,
                state=EVENT_STATE_INVALID if is_active else EVENT_STATE_VALID,
            )

    @property
    def is_active(self) -> bool:
        return self.state == self.STATE_ACTIVE

    async def set_active(self, active: bool):
        """Update sensor state and produce corresponding OpenLCB PCER event."""
        new_state = self.STATE_ACTIVE if active else self.STATE_INACTIVE
        if new_state == self.state:
            return

        self.state = new_state

        if self.node and self.node.event_manager:
            if self.active_event:
                self.node.event_manager.set_producer_state(
                    self.active_event,
                    EVENT_STATE_VALID if active else EVENT_STATE_INVALID,
                )
            if self.inactive_event:
                self.node.event_manager.set_producer_state(
                    self.inactive_event,
                    EVENT_STATE_INVALID if active else EVENT_STATE_VALID,
                )

            # Broadcast PCER event to layout
            event_to_send = self.active_event if active else self.inactive_event
            if event_to_send:
                await self.node.produce_event(event_to_send)

        await self._notify_change(self.state)

    async def trigger(self, active: bool):
        """Convenience alias for set_active."""
        await self.set_active(active)


# Aliases for railroad terminology
BlockDetector = Sensor
Input = Sensor


# --- Signal / SignalMast Component ---

class Signal(BaseComponent):
    """
    OpenLCB Signal / Signal Mast.
    Consumes aspect events (e.g. STOP, APPROACH, CLEAR, RESTRICTING) and updates state.
    """
    def __init__(
        self,
        name: str = "",
        aspect_events: dict = None,
        initial_aspect: str = "STOP",
        on_change=None,
    ):
        super().__init__(name=name, on_change=on_change)
        # aspect_name -> EventID
        self.aspect_events = {}
        if aspect_events:
            for asp, eid in aspect_events.items():
                self.aspect_events[asp] = EventID(eid)
        self.current_aspect = initial_aspect

    def setup_events(self):
        if not self.node:
            return

        for aspect_name, event_id in self.aspect_events.items():
            is_current = (aspect_name == self.current_aspect)
            # Create closure for aspect handler
            def make_handler(aspect):
                async def handler(eid):
                    await self.set_aspect(aspect)
                return handler

            self.node.event_manager.register_consumer(
                event_id,
                make_handler(aspect_name),
                state=EVENT_STATE_VALID if is_current else EVENT_STATE_INVALID,
            )

    async def set_aspect(self, aspect: str):
        """Change current aspect and notify callbacks."""
        if aspect == self.current_aspect:
            return

        self.current_aspect = aspect

        # Update consumer states in EventManager
        if self.node and self.node.event_manager:
            for asp, eid in self.aspect_events.items():
                self.node.event_manager.set_consumer_state(
                    eid,
                    EVENT_STATE_VALID if asp == aspect else EVENT_STATE_INVALID,
                )

        await self._notify_change(aspect)


# Alias
SignalMast = Signal


# --- Output / Light Component ---

class Output(BaseComponent):
    """
    OpenLCB Discrete Output / Light / Uncoupler / Relay.
    Consumes On and Off events.
    """
    STATE_OFF = "OFF"
    STATE_ON = "ON"

    def __init__(
        self,
        name: str = "",
        on_event=None,
        off_event=None,
        initial_state: str = STATE_OFF,
        on_change=None,
    ):
        super().__init__(name=name, on_change=on_change)
        self.on_event = EventID(on_event) if on_event else None
        self.off_event = EventID(off_event) if off_event else None
        self.state = initial_state

    def setup_events(self):
        if not self.node:
            return

        is_on = (self.state == self.STATE_ON)
        if self.on_event:
            self.node.event_manager.register_consumer(
                self.on_event,
                self._handle_on_event,
                state=EVENT_STATE_VALID if is_on else EVENT_STATE_INVALID,
            )
        if self.off_event:
            self.node.event_manager.register_consumer(
                self.off_event,
                self._handle_off_event,
                state=EVENT_STATE_INVALID if is_on else EVENT_STATE_VALID,
            )

    async def _handle_on_event(self, eid: EventID):
        await self.set_on(True)

    async def _handle_off_event(self, eid: EventID):
        await self.set_on(False)

    @property
    def is_on(self) -> bool:
        return self.state == self.STATE_ON

    async def set_on(self, on: bool):
        """Set output ON or OFF."""
        new_state = self.STATE_ON if on else self.STATE_OFF
        if new_state == self.state:
            return

        self.state = new_state

        if self.node and self.node.event_manager:
            if self.on_event:
                self.node.event_manager.set_consumer_state(
                    self.on_event,
                    EVENT_STATE_VALID if on else EVENT_STATE_INVALID,
                )
            if self.off_event:
                self.node.event_manager.set_consumer_state(
                    self.off_event,
                    EVENT_STATE_INVALID if on else EVENT_STATE_VALID,
                )

        await self._notify_change(self.state)


# Alias
Light = Output
