"""
Interactive OpenLCB / JMRI Node Demo.
Demonstrates a multi-function layout node equipped with:
- 2 Turnouts (with feedback)
- 2 Block Occupancy Detectors
- 1 3-Aspect Signal Mast (STOP, APPROACH, CLEAR)
- 1 Lighting Output (Yard floodlight)
- Custom CDI XML schema with configuration variables (servo travel, debounce)
- Flash persistence (saves user config edits from JMRI DecoderPro CDI window)
"""

import asyncio
import sys
from pylcc import (
    LCCNode,
    LCCTurnout,
    LCCSensor,
    LCCSignal,
    LCCOutput,
    SnipData,
    EventID,
    NodeID,
    CdiDocument,
    SPACE_CONFIG,
)
from pylcc.transport.tcp import GridConnectTcpClient


def build_custom_cdi(snip: SnipData, node_id: NodeID) -> CdiDocument:
    """Build a rich, structured CDI document with layout controls and user settings."""
    doc = CdiDocument(
        manufacturer=snip.manufacturer,
        model=snip.model,
        hardware_version=snip.hardware_version,
        software_version=snip.software_version,
    )

    seg = doc.add_segment(space=SPACE_CONFIG, origin=0, name="Configuration", description="Node Hardware Settings")
    seg.add_string("node_name", name="Node Name", description="User-assigned name", size=20, default=snip.user_name)

    # Turnout group with configurable servo travel speeds
    turnout_grp = seg.add_group("turnouts", name="Turnout Settings", description="Switch machine parameters")
    turnout_grp.add_int("servo_speed", name="Servo Speed (ms/deg)", size=1, min_val=1, max_val=50, default=10)
    t1_grp = turnout_grp.add_group("turnout_1", name="Turnout 1 (Mainline)")
    t1_grp.add_eventid("t1_close", name="Close Event", default=EventID.from_node_id(node_id, 0x01))
    t1_grp.add_eventid("t1_throw", name="Throw Event", default=EventID.from_node_id(node_id, 0x02))

    # Sensor group with configurable debounce filter
    sensor_grp = seg.add_group("sensors", name="Sensor Settings", description="Track occupancy detection")
    sensor_grp.add_int("debounce_ms", name="Debounce Delay (ms)", size=2, min_val=10, max_val=1000, default=50)
    s1_grp = sensor_grp.add_group("sensor_1", name="Block Detector 1")
    s1_grp.add_eventid("s1_act", name="Active Event", default=EventID.from_node_id(node_id, 0x10))
    s1_grp.add_eventid("s1_inact", name="Inactive Event", default=EventID.from_node_id(node_id, 0x11))

    # Signal Mast group
    signal_grp = seg.add_group("signals", name="Signal Mast Settings")
    signal_grp.add_eventid("sig_stop", name="Aspect STOP Event", default=EventID.from_node_id(node_id, 0x20))
    signal_grp.add_eventid("sig_app", name="Aspect APPROACH Event", default=EventID.from_node_id(node_id, 0x21))
    signal_grp.add_eventid("sig_clr", name="Aspect CLEAR Event", default=EventID.from_node_id(node_id, 0x22))

    doc.build()
    return doc


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021
    node_id = NodeID(sys.argv[3] if len(sys.argv) > 3 else "05.01.01.01.14.07")

    print(f"=== pyLCC Interactive Node [{node_id}] ===")
    print(f"Connecting to GridConnect TCP at {host}:{port}...")

    transport = GridConnectTcpClient(host=host, port=port)

    snip = SnipData(
        manufacturer="SVL Railroad",
        model="pyLCC Multi-IO Board",
        hardware_version="Rev C",
        software_version="0.1.0",
        user_name="Tower Interlocking Node",
        user_description="Mainline Turnouts, Signals, and Track Sensors",
    )

    cdi = build_custom_cdi(snip, node_id)

    node = LCCNode(
        node_id=node_id,
        transport=transport,
        snip=snip,
        cdi=cdi,
        config_file="tower_node_config.bin",
    )

    # 1. Attach Turnouts
    t1 = LCCTurnout(
        name="Mainline Turnout",
        close_event=EventID.from_node_id(node_id, 0x01),
        throw_event=EventID.from_node_id(node_id, 0x02),
        is_closed_event=EventID.from_node_id(node_id, 0x03),
        is_thrown_event=EventID.from_node_id(node_id, 0x04),
        on_change=lambda st: print(f"  >>> [TURNOUT] Mainline switch position changed: {st}"),
    )
    node.add_component(t1)

    # 2. Attach Sensors
    s1 = LCCSensor(
        name="Mainline Block 1",
        active_event=EventID.from_node_id(node_id, 0x10),
        inactive_event=EventID.from_node_id(node_id, 0x11),
        on_change=lambda st: print(f"  >>> [DETECTOR] Block 1 occupancy changed: {st}"),
    )
    node.add_component(s1)

    # 3. Attach Signal Mast
    sig = LCCSignal(
        name="Eastbound Signal",
        aspect_events={
            "STOP": EventID.from_node_id(node_id, 0x20),
            "APPROACH": EventID.from_node_id(node_id, 0x21),
            "CLEAR": EventID.from_node_id(node_id, 0x22),
        },
        initial_aspect="STOP",
        on_change=lambda asp: print(f"  >>> [SIGNAL] Signal aspect changed: {asp}"),
    )
    node.add_component(sig)

    # 4. Attach Yard Light
    light = LCCOutput(
        name="Yard Floodlight",
        on_event=EventID.from_node_id(node_id, 0x30),
        off_event=EventID.from_node_id(node_id, 0x31),
        on_change=lambda st: print(f"  >>> [LIGHT] Floodlight power state: {st}"),
    )
    node.add_component(light)

    # Start node
    await node.start()
    print(f"\nNode Online with Alias 0x{node.alias:03X}!")
    print("JMRI can now query this node, open its CDI configuration, and operate turnouts/signals.")
    print("\nRunning interactive layout loop (press Ctrl+C to terminate)...")

    # Simulate periodic background activity
    try:
        step = 0
        while True:
            await asyncio.sleep(5)
            step += 1
            if step % 3 == 0:
                # Toggle sensor occupancy
                new_state = not s1.is_active
                print(f"\n[EVENT] Simulating sensor occupancy -> {new_state}")
                await s1.set_active(new_state)
            elif step % 3 == 1:
                # Cycle signal aspect
                aspects = ["STOP", "APPROACH", "CLEAR"]
                next_asp = aspects[(aspects.index(sig.current_aspect) + 1) % len(aspects)]
                print(f"\n[EVENT] Cycling signal aspect -> {next_asp}")
                await sig.set_aspect(next_asp)
    except asyncio.CancelledError:
        pass
    finally:
        print("\nShutting down node...")
        await node.stop()
        print("Clean shutdown completed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
