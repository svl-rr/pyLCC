"""
Virtual Turnout & Block Detector Node Demo.
Demonstrates OpenLCB Event Transport (Producer / Consumer model):
- Consumes Turnout Thrown / Closed commands.
- Produces Block Detector Occupied / Clear events.
- Responds to Identify Events queries from JMRI or other nodes.
"""

import asyncio
import sys
from pylcc.node_id import NodeID
from pylcc.event_id import EventID
from pylcc.alias import AliasManager
from pylcc.events import EventManager
from pylcc.transport.tcp import GridConnectTcpClient
from pylcc.gridconnect import encode_gridconnect


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021
    node_id = NodeID(sys.argv[3] if len(sys.argv) > 3 else "05.01.01.01.14.05")

    print(f"=== pyLCC Virtual Turnout & Sensor Node [{node_id}] ===")
    print(f"Connecting to GridConnect TCP at {host}:{port}...")

    transport = GridConnectTcpClient(host=host, port=port)
    await transport.connect()

    alias_manager = AliasManager(node_id, transport)
    event_manager = EventManager(node_id, alias_manager, transport)

    # Route incoming CAN frames to both AliasManager and EventManager
    def on_frame(frame):
        asyncio.create_task(alias_manager.process_frame(frame))
        asyncio.create_task(event_manager.process_frame(frame))

    transport.add_listener(on_frame)

    # 1. Allocate CAN alias
    print("Allocating CAN Alias...")
    alias = await alias_manager.allocate(check_delay_ms=100)
    print(f"Node active with Alias 0x{alias:03X}!\n")

    # 2. Define Event IDs (using node_id as prefix + 16-bit indices)
    event_turnout_thrown = EventID.from_node_id(node_id, 0x0001)
    event_turnout_closed = EventID.from_node_id(node_id, 0x0002)
    event_block_occupied = EventID.from_node_id(node_id, 0x0010)
    event_block_cleared  = EventID.from_node_id(node_id, 0x0011)

    # 3. Register Consumers (Turnout commands)
    def on_turnout_thrown(eid):
        print(f"\n[ACTION] Turnout command received: THROWN ({eid})")

    def on_turnout_closed(eid):
        print(f"\n[ACTION] Turnout command received: CLOSED ({eid})")

    event_manager.register_consumer(event_turnout_thrown, callback=on_turnout_thrown)
    event_manager.register_consumer(event_turnout_closed, callback=on_turnout_closed)

    # 4. Register Producers (Detector states)
    event_manager.register_producer(event_block_occupied)
    event_manager.register_producer(event_block_cleared)

    print("Registered Events:")
    print(f"  [CONSUMER] Turnout Thrown : {event_turnout_thrown}")
    print(f"  [CONSUMER] Turnout Closed : {event_turnout_closed}")
    print(f"  [PRODUCER] Block Occupied : {event_block_occupied}")
    print(f"  [PRODUCER] Block Cleared  : {event_block_cleared}")
    print("\nNode is running. Toggling block occupancy every 5 seconds (Press Ctrl+C to stop)...")

    occupied = False
    try:
        while True:
            await asyncio.sleep(5)
            occupied = not occupied
            active_event = event_block_occupied if occupied else event_block_cleared
            desc = "OCCUPIED" if occupied else "CLEARED"
            print(f"\n[PRODUCING EVENT] Block is now {desc} -> {active_event}")
            await event_manager.produce(active_event)
    except asyncio.CancelledError:
        pass
    finally:
        print("\nReleasing alias...")
        await alias_manager.release()
        await transport.close()
        print("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
