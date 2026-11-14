"""
OpenLCB Node ID Alias Allocation Protocol.
Compliant with NMRA Standard S-9.7.2.1 and Technical Note TN-9.7.2.1.
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

from .can import CanFrame
from .node_id import NodeID


# States for Node Alias allocation
STATE_INHIBITED = "INHIBITED"
STATE_CHECKING = "CHECKING"
STATE_PERMITTED = "PERMITTED"


class AliasPrng:
    """
    48-bit Pseudo-Random Number Generator (PRNG) defined by Heidi G. Kuehn (1961),
    specified in OpenLCB TN-9.7.2.1 for non-colliding alias generation.
    x_{i+1} = (513 * x_i + 0x1B0CA37A4BA9) mod 2^48
    """
    __slots__ = ("_state",)
    MULTIPLIER = 513
    ADDEND = 0x1B0CA37A4BA9
    MASK_48 = 0xFFFFFFFFFFFF

    def __init__(self, seed: int):
        self._state = seed & self.MASK_48

    @property
    def state(self) -> int:
        return self._state

    def step(self):
        """Advance the PRNG state by one iteration."""
        self._state = (self._state * self.MULTIPLIER + self.ADDEND) & self.MASK_48

    def next_alias(self) -> int:
        """
        Derive the next 12-bit CAN alias by folding the 48-bit state.
        Never returns 0 (alias 0 is reserved/forbidden).
        """
        while True:
            self.step()
            alias = (
                (self._state & 0x0FFF)
                ^ ((self._state >> 12) & 0x0FFF)
                ^ ((self._state >> 24) & 0x0FFF)
                ^ ((self._state >> 36) & 0x0FFF)
            )
            if alias != 0:
                return alias


class AliasManager:
    """
    Manages the lifecycle of a node's 12-bit CAN alias on a local bus segment:
    Inhibited -> Checking (CID 7..4 + 100ms timer) -> Permitted (RID + AMD).
    Defends alias against collisions and responds to Alias Mapping Enquiries (AME).
    """
    __slots__ = (
        "node_id",
        "transport",
        "prng",
        "alias",
        "state",
        "_collision_event",
        "_check_timer_task",
        "_on_permitted_callbacks",
    )
    def __init__(self, node_id, transport):
        self.node_id = NodeID(node_id)
        self.transport = transport
        self.prng = AliasPrng(self.node_id.value)
        self.alias = 0
        self.state = STATE_INHIBITED

        self._collision_event = asyncio.Event()
        self._check_timer_task = None
        self._on_permitted_callbacks = []

    def add_on_permitted(self, callback):
        """Register a callback(alias) called whenever the alias becomes PERMITTED."""
        if callback not in self._on_permitted_callbacks:
            self._on_permitted_callbacks.append(callback)

    @property
    def is_permitted(self) -> bool:
        return self.state == STATE_PERMITTED

    async def allocate(self, check_delay_ms: int = 100) -> int:
        """
        Run the alias allocation sequence.
        Returns the allocated 12-bit alias once PERMITTED.
        """
        while self.state != STATE_PERMITTED:
            self.state = STATE_INHIBITED
            self._collision_event.clear()
            self.alias = self.prng.next_alias()

            # Broadcast CID frames in sequence 7 -> 6 -> 5 -> 4
            for seq in (7, 6, 5, 4):
                chunk = self.node_id.get_chunk(seq)
                cid_frame = CanFrame.make_cid(sequence=seq, nid_chunk=chunk, alias=self.alias)
                await self.transport.send_frame(cid_frame)

            self.state = STATE_CHECKING

            # Wait for 100 ms (or custom test delay) while watching for collisions
            collision_detected = False
            try:
                # Wait for collision or timeout
                await asyncio.wait_for(self._collision_event.wait(), timeout=check_delay_ms / 1000.0)
                # If we get here without timeout, collision occurred!
                collision_detected = True
            except asyncio.TimeoutError:
                # 100 ms elapsed with no collision
                collision_detected = False

            if collision_detected:
                # Conflict occurred, try next candidate alias
                continue

            # No collision! Reserve and announce alias
            rid_frame = CanFrame.make_rid(self.alias)
            await self.transport.send_frame(rid_frame)

            amd_frame = CanFrame.make_amd(self.alias, self.node_id.to_bytes())
            await self.transport.send_frame(amd_frame)

            self.state = STATE_PERMITTED

            for cb in self._on_permitted_callbacks:
                try:
                    cb(self.alias)
                except Exception:
                    pass

        return self.alias

    async def process_frame(self, frame: CanFrame):
        """
        Handle incoming CAN frame relevant to alias allocation or defense.
        Should be hooked to transport reception.
        """
        if frame.source_alias == self.alias and self.alias != 0:
            if self.state == STATE_CHECKING:
                # Another node claims or is checking this alias!
                if frame.is_rid or frame.is_amd or frame.is_cid:
                    self._collision_event.set()

            elif self.state == STATE_PERMITTED:
                if frame.is_cid:
                    # A newcomer is checking our claimed alias! Defend it with RID.
                    rid_frame = CanFrame.make_rid(self.alias)
                    await self.transport.send_frame(rid_frame)
                elif frame.is_amd or frame.is_openlcb_message or frame.is_datagram:
                    # Collision with an active node! Release alias and reallocate.
                    amr_frame = CanFrame.make_amr(self.alias, self.node_id.to_bytes())
                    await self.transport.send_frame(amr_frame)
                    self.state = STATE_INHIBITED
                    asyncio.create_task(self.allocate())

        # Respond to Alias Mapping Enquiry (AME)
        if frame.is_ame and self.state == STATE_PERMITTED:
            # Empty payload queries all nodes; non-empty queries specific Node ID
            if not frame.data or frame.data == self.node_id.to_bytes():
                amd_frame = CanFrame.make_amd(self.alias, self.node_id.to_bytes())
                await self.transport.send_frame(amd_frame)

    async def release(self):
        """Release alias upon node shutdown or disconnect."""
        if self.state == STATE_PERMITTED and self.alias != 0:
            amr_frame = CanFrame.make_amr(self.alias, self.node_id.to_bytes())
            await self.transport.send_frame(amr_frame)
        self.state = STATE_INHIBITED
        self.alias = 0
