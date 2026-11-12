"""
Virtual Turnout & Block Detector Node Demo.
Demonstrates the high-level pyLCC Node and Component stack:
- Uses LCCNode, LCCTurnout, and LCCSensor.
- Automatically generates CDI XML served to JMRI over Datagram Space 0xFF.
- Persists user settings to configuration storage (config.bin).
- Handles Turnout Thrown / Closed events with feedback.
- Produces Block Detector Occupied / Cleared events.
- Answers Identify inquiries and SNIP/PIP protocol requests.
"""

import asyncio
import sys
from pylcc import (
    LCCNode,
    LCCTurnout,
    LCCSensor,
    SnipData,
    EventID,
    NodeID,
)
from pylcc.transport.tcp import GridConnectTcpClient


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021
    node_id_str = sys.argv[3] if len(sys.argv) > 3 else "05.01.01.01.14.05"
    node_id = NodeID(node_id_str)

    print(f"=== pyLCC Virtual Turnout & Sensor Node [{node_id}] ===")
    print(f"Connecting to GridConnect TCP at {host}:{port}...")

    transport = GridConnectTcpClient(host=host, port=port)

    # 1. Provide SNIP identification (displayed in JMRI Node Tree)
    snip = SnipData(
        manufacturer="SVL Layout Systems",
        model="pyLCC Virtual 2T-2S",
        hardware_version="v1.0-Sim",
        software_version="0.1.0",
        user_name="Yard Switch Machine",
        user_description="Simulated Turnout & Sensor Controller",
    )

    # 2. Instantiate high-level LCC Node orchestrator
    node = LCCNode(
        node_id=node_id,
        transport=transport,
        snip=snip,
        config_file="virtual_node_config.bin",
    )

    # 3. Define Turnouts
    # Turnout 1: Mainline Crossover
    t1_close = EventID.from_node_id(node_id, 0x0001)
    t1_throw = EventID.from_node_id(node_id, 0x0002)
    t1_is_close = EventID.from_node_id(node_id, 0x0003)
    t1_is_throw = EventID.from_node_id(node_id, 0x0004)

    def on_turnout1_change(state):
        print(f"  [HARDWARE] Turnout 1 servo moving to: {state}")

    turnout1 = LCCTurnout(
        name="Mainline Crossover",
        close_event=t1_close,
        throw_event=t1_throw,
        is_closed_event=t1_is_close,
        is_thrown_event=t1_is_throw,
        initial_state=LCCTurnout.STATE_CLOSED,
        on_change=on_turnout1_change,
    )
    node.add_component(turnout1)

    # Turnout 2: Siding Switch
    t2_close = EventID.from_node_id(node_id, 0x0005)
    t2_throw = EventID.from_node_id(node_id, 0x0006)
    turnout2 = LCCTurnout(
        name="Siding Switch",
        close_event=t2_close,
        throw_event=t2_throw,
        initial_state=LCCTurnout.STATE_CLOSED,
        on_change=lambda state: print(f"  [HARDWARE] Turnout 2 motor: {state}"),
    )
    node.add_component(turnout2)

    # 4. Define Sensors
    # Sensor 1: Mainline Block Occupancy
    s1_active = EventID.from_node_id(node_id, 0x0010)
    s1_inactive = EventID.from_node_id(node_id, 0x0011)
    sensor1 = LCCSensor(
        name="Mainline Block 1",
        active_event=s1_active,
        inactive_event=s1_inactive,
        initial_state=LCCSensor.STATE_INACTIVE,
        on_change=lambda state: print(f"  [DETECTOR] Block 1 state changed: {state}"),
    )
    node.add_component(sensor1)

    # Sensor 2: Local Control Panel Pushbutton
    s2_active = EventID.from_node_id(node_id, 0x0020)
    s2_inactive = EventID.from_node_id(node_id, 0x0021)
    sensor2 = LCCSensor(
        name="Panel Pushbutton 1",
        active_event=s2_active,
        inactive_event=s2_inactive,
        initial_state=LCCSensor.STATE_INACTIVE,
    )
    node.add_component(sensor2)

    # 5. Start the Node (auto-generates CDI, allocates CAN alias, sends Init Complete)
    print("\nStarting OpenLCB Node...")
    await node.start()
    print(f">>> Node Online! Allocated Alias: 0x{node.alias:03X} <<<")
    print(f"CDI XML generated and served on Datagram Space 0xFF ({len(node.cdi_doc.to_bytes())} bytes)")
    print("\nConfigured Layout Components:")
    print(f"  • Turnout 1: '{turnout1.name}' (Close: {t1_close}, Throw: {t1_throw})")
    print(f"  • Turnout 2: '{turnout2.name}' (Close: {t2_close}, Throw: {t2_throw})")
    print(f"  • Sensor 1 : '{sensor1.name}' (Active: {s1_active}, Inactive: {s1_inactive})")
    print(f"  • Sensor 2 : '{sensor2.name}' (Active: {s2_active}, Inactive: {s2_inactive})")
    print("\nNode is active. Simulating train traffic across Block 1 every 6 seconds.")
    print("JMRI can discover this node, view its CDI dialog, and control turnouts.")
    print("Press Ctrl+C to stop.\n")

    occupied = False
    try:
        while True:
            await asyncio.sleep(6)
            occupied = not occupied
            state_desc = "OCCUPIED" if occupied else "CLEARED"
            print(f"[SIMULATED TRAIN] Train enters/leaves block -> Setting Block 1 to {state_desc}")
            await sensor1.set_active(occupied)
    except asyncio.CancelledError:
        pass
    finally:
        print("\nShutting down node (releasing CAN alias)...")
        await node.stop()
        print("Node offline.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
