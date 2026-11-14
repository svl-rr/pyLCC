"""
pyLCC Unified Node Orchestrator.
Encapsulates Transport, Alias Allocation, Events, Datagrams, SNIP, Memory Config, and Components.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .can import CanFrame
from .node_id import NodeID
from .event_id import EventID
from .alias import AliasManager, STATE_PERMITTED
from .events import EventManager, EVENT_STATE_VALID, EVENT_STATE_INVALID, EVENT_STATE_UNKNOWN
from .datagram import DatagramManager
from .snip import SnipData, SnipService
from .memory import (
    MemoryConfigurationService,
    SPACE_CDI,
    SPACE_CONFIG,
    SPACE_ACDI_MFG,
    SPACE_ACDI_USER,
    BytesMemorySpace,
    ConfigMemorySpace,
    AcdiManufacturerSpace,
    AcdiUserSpace,
)
from .cdi import CdiDocument
from .components.base import BaseComponent
from .components.devices import Turnout, Sensor, Signal, Output

# MTI for Initialization Complete (Simple: 0x0100, Full: 0x0101)
MTI_INITIALIZATION_COMPLETE = 0x0100


class Node:
    """
    High-level OpenLCB / LCC Node.
    Coordinates all protocol layers and manages attached layout components.
    """
    def __init__(
        self,
        node_id,
        transport,
        snip: SnipData = None,
        cdi: CdiDocument = None,
        config_file: str = None,
        on_restart=None,
    ):
        self.node_id = NodeID(node_id)
        self.transport = transport
        self.snip_data = snip or SnipData(
            manufacturer="pyLCC",
            model="OpenLCB Node",
            hardware_version="1.0",
            software_version="0.1.0",
            user_name="pyLCC Node",
            user_description="MicroPython OpenLCB Node",
        )
        self.cdi_doc = cdi
        self.config_file = config_file
        self.on_restart = on_restart

        # Instantiate core protocol services
        self.alias_manager = AliasManager(self.node_id, self.transport)
        self.event_manager = EventManager(self.node_id, self.alias_manager, self.transport)
        self.datagram_manager = DatagramManager(self.node_id, self.alias_manager, self.transport)
        self.snip_service = SnipService(self.node_id, self.alias_manager, self.transport, snip_data=self.snip_data)
        self.memory_service = MemoryConfigurationService(self.node_id, self.alias_manager, self.datagram_manager)

        # Wire restart callback if provided
        if self.on_restart:
            self.memory_service.set_on_restart(self.on_restart)

        # Memory spaces (register ACDI Mfg 0xFC and User 0xFB by default)
        self.acdi_mfg_space = AcdiManufacturerSpace(self.snip_data)
        self.acdi_user_space = AcdiUserSpace(self.snip_data)
        self.memory_service.register_space(self.acdi_mfg_space)
        self.memory_service.register_space(self.acdi_user_space)

        self.cdi_space = None
        self.config_space = None
        if self.cdi_doc:
            self._setup_cdi_spaces(self.cdi_doc)

        # Attached components
        self.components = []
        self._components_by_name = {}

        # Lifecycle state
        self._is_started = False
        self._frame_listener = None

    @property
    def alias(self) -> int:
        """The currently allocated 12-bit CAN alias."""
        return self.alias_manager.alias

    @property
    def is_permitted(self) -> bool:
        """True if the node has successfully acquired a permitted CAN alias."""
        return self.alias_manager.is_permitted

    def _setup_cdi_spaces(self, cdi_doc: CdiDocument):
        """Build and register CDI (Space 0xFF) and Config (Space 0xFD) memory spaces."""
        self.cdi_space = cdi_doc.create_cdi_space()
        self.config_space = cdi_doc.create_config_space(
            filename=self.config_file,
            min_size=512,
        )
        self.memory_service.register_space(self.cdi_space)
        self.memory_service.register_space(self.config_space)

    # --- Component Management ---

    def add_component(self, component: BaseComponent):
        """Attach a layout component (Turnout, Sensor, Signal, Output) to this node."""
        self.components.append(component)
        if component.name:
            self._components_by_name[component.name] = component
        component.bind(self)
        return component

    def get_component(self, name: str) -> BaseComponent:
        """Find an attached component by name."""
        return self._components_by_name.get(name)

    # --- Automatic CDI Generation ---

    def auto_generate_cdi(self) -> CdiDocument:
        """
        Generate a CdiDocument from attached components if no custom CDI was provided.
        Ensures JMRI can discover and configure all node components via CDI.
        """
        doc = CdiDocument(
            manufacturer=self.snip_data.manufacturer,
            model=self.snip_data.model,
            hardware_version=self.snip_data.hardware_version,
            software_version=self.snip_data.software_version,
        )

        seg = doc.add_segment(space=SPACE_CONFIG, origin=0, name="Configuration", description="Node Settings")
        seg.add_string("node_desc", name="Description", description="User node description", size=20, default=self.snip_data.user_name)

        # Add groups for attached components
        for i, comp in enumerate(self.components):
            safe_name = comp.name or f"Component_{i+1}"
            comp_key = f"comp_{i+1}"

            if isinstance(comp, Turnout):
                grp = seg.add_group(comp_key, name=safe_name, description="Turnout Configuration")
                if comp.close_event:
                    grp.add_eventid(f"{comp_key}_close", name="Close Event", default=comp.close_event)
                if comp.throw_event:
                    grp.add_eventid(f"{comp_key}_throw", name="Throw Event", default=comp.throw_event)
                if comp.is_closed_event:
                    grp.add_eventid(f"{comp_key}_is_closed", name="Feedback Closed", default=comp.is_closed_event)
                if comp.is_thrown_event:
                    grp.add_eventid(f"{comp_key}_is_thrown", name="Feedback Thrown", default=comp.is_thrown_event)

            elif isinstance(comp, Sensor):
                grp = seg.add_group(comp_key, name=safe_name, description="Sensor / Detector Configuration")
                if comp.active_event:
                    grp.add_eventid(f"{comp_key}_act", name="Active Event", default=comp.active_event)
                if comp.inactive_event:
                    grp.add_eventid(f"{comp_key}_inact", name="Inactive Event", default=comp.inactive_event)

            elif isinstance(comp, Signal):
                grp = seg.add_group(comp_key, name=safe_name, description="Signal Mast Configuration")
                for asp_name, asp_eid in comp.aspect_events.items():
                    grp.add_eventid(f"{comp_key}_{asp_name.lower()}", name=f"Aspect {asp_name}", default=asp_eid)

            elif isinstance(comp, Output):
                grp = seg.add_group(comp_key, name=safe_name, description="Discrete Output Configuration")
                if comp.on_event:
                    grp.add_eventid(f"{comp_key}_on", name="On Event", default=comp.on_event)
                if comp.off_event:
                    grp.add_eventid(f"{comp_key}_off", name="Off Event", default=comp.off_event)

        doc.build()
        self.cdi_doc = doc
        self._setup_cdi_spaces(doc)
        return doc

    # --- Incoming Frame Dispatcher ---

    async def _handle_incoming_frame(self, frame: CanFrame):
        """Unified sequential processing of an incoming frame across all protocol engines."""
        await self.alias_manager.process_frame(frame)
        await self.event_manager.process_frame(frame)
        await self.datagram_manager.process_frame(frame)
        await self.snip_service.process_frame(frame)

    def _dispatch_frame(self, frame: CanFrame):
        """
        Synchronous fast-path frame filter and router.
        Drops irrelevant bus traffic before allocating an asyncio task.
        """
        alias = self.alias
        if frame.source_alias == alias and alias != 0:
            asyncio.create_task(self.alias_manager.process_frame(frame))
            return

        if frame.is_can_control:
            if frame.is_ame and self.alias_manager.is_permitted:
                if not frame.data or frame.data == self.node_id.to_bytes():
                    asyncio.create_task(self.alias_manager.process_frame(frame))
            return

        if frame.is_datagram:
            if frame.destination_alias == alias:
                asyncio.create_task(self.datagram_manager.process_frame(frame))
            return

        if frame.is_openlcb_message:
            if frame.is_addressed:
                if frame.destination_alias != alias:
                    return
                mti = frame.mti
                if mti == 0x0A28 or mti == 0x0A48:
                    asyncio.create_task(self.datagram_manager.process_frame(frame))
                elif mti == 0x0828 or mti == 0x0DE8:
                    asyncio.create_task(self.snip_service.process_frame(frame))
                elif self.event_manager.has_interest(frame):
                    asyncio.create_task(self.event_manager.process_frame(frame))
            elif self.event_manager.has_interest(frame):
                asyncio.create_task(self.event_manager.process_frame(frame))

    # --- Lifecycle ---

    async def start(self, check_delay_ms: int = 100):
        """
        Start the node:
        1. Connect transport (if applicable).
        2. Auto-generate CDI if none provided.
        3. Attach frame listener.
        4. Allocate CAN alias via PRNG sequence.
        5. Broadcast Initialization Complete.
        """
        if self._is_started:
            return

        # Connect transport if not connected
        if hasattr(self.transport, "connect"):
            if hasattr(self.transport, "is_connected"):
                if not self.transport.is_connected:
                    await self.transport.connect()
            else:
                await self.transport.connect()

        # If no CDI document provided, auto-generate from attached components
        if not self.cdi_doc:
            self.auto_generate_cdi()

        # Attach frame listener
        self._frame_listener = self._dispatch_frame
        self.transport.add_listener(self._frame_listener)

        # Allocate 12-bit CAN alias
        await self.alias_manager.allocate(check_delay_ms=check_delay_ms)

        # Broadcast Initialization Complete frame (MTI 0x0100)
        init_frame = CanFrame.make_global_message(
            mti=MTI_INITIALIZATION_COMPLETE,
            source_alias=self.alias,
            data=self.node_id.to_bytes(),
        )
        await self.transport.send_frame(init_frame)

        # Announce any registered Consumer/Producer Event Ranges
        await self.event_manager.broadcast_ranges()

        self._is_started = True

    async def stop(self):
        """Release alias, detach listener, and disconnect transport."""
        if not self._is_started:
            return

        if self.is_permitted:
            await self.alias_manager.release()

        if self._frame_listener:
            try:
                self.transport.remove_listener(self._frame_listener)
            except Exception:
                pass
            self._frame_listener = None

        if hasattr(self.transport, "close"):
            await self.transport.close()

        self._is_started = False

    # --- Convenience Event API ---

    async def produce_event(self, event_id):
        """Broadcast a Producer/Consumer Event Report (PCER) to the layout."""
        await self.event_manager.produce(event_id)

    def register_consumer(self, event_id, callback, state: int = EVENT_STATE_UNKNOWN):
        """Register a consumer callback for an EventID."""
        self.event_manager.register_consumer(event_id, callback, state=state)

    def register_producer(self, event_id, state: int = EVENT_STATE_UNKNOWN):
        """Register a produced EventID with initial state."""
        self.event_manager.register_producer(event_id, state=state)

    def register_consumer_range(self, base_event_id, size: int, callback=None):
        """Register a contiguous power-of-two consumer EventID range (0x04A4)."""
        return self.event_manager.register_consumer_range(base_event_id, size, callback=callback)

    def register_producer_range(self, base_event_id, size: int):
        """Register a contiguous power-of-two producer EventID range (0x0524)."""
        return self.event_manager.register_producer_range(base_event_id, size)

    async def send_datagram(self, dest_alias: int, data: bytes, timeout_ms: int = 3000) -> bool:
        """Send a datagram to target node alias."""
        return await self.datagram_manager.send_datagram(dest_alias, data, timeout_ms=timeout_ms)


# Alias for explicit LCC layout naming and namespace collision prevention
LCCNode = Node

