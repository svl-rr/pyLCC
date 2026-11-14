"""
MicroPython WiFi LCC Node Demo.
Designed for any networked microcontroller:
- ESP32 / ESP32-C3 / ESP32-C6 / ESP32-S3 (WiFi)
- Raspberry Pi Pico W / Pico 2 W (WiFi)
- STM32 (with WiFi / Ethernet)

Connects to WiFi and communicates OpenLCB / LCC over TCP (GridConnect protocol)
with JMRI or an LCC Hub, requiring zero physical CAN wiring!
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

import sys

# Attempt MicroPython network module
try:
    import network
except ImportError:
    network = None

from pylcc import (
    LCCNode,
    LCCTurnout,
    LCCSensor,
    SnipData,
    EventID,
    NodeID,
)
from pylcc.transport.tcp import GridConnectTcpClient


# --- WiFi Configuration ---
WIFI_SSID = "Your_WiFi_SSID"
WIFI_PASSWORD = "Your_WiFi_Password"

# LCC Hub / JMRI endpoint
LCC_HUB_IP = "192.168.1.100"
LCC_HUB_PORT = 12021


async def connect_wifi(ssid: str, password: str, timeout_sec: int = 15) -> bool:
    """Connect MicroPython device to local WiFi network."""
    if network is None:
        print("[WIFI] Network module not available (running in simulation/desktop mode).")
        return True

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print(f"[WIFI] Already connected. IP: {wlan.ifconfig()[0]}")
        return True

    print(f"[WIFI] Connecting to '{ssid}'...")
    wlan.connect(ssid, password)

    for _ in range(timeout_sec * 2):
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print(f"[WIFI] Connected successfully! IP Address: {ip}")
            return True
        await asyncio.sleep(0.5)

    print("[WIFI] Connection failed or timed out!")
    return False


async def main():
    print("=== pyLCC MicroPython Wireless Node ===")

    # 1. Connect to WiFi
    wifi_ok = await connect_wifi(WIFI_SSID, WIFI_PASSWORD)
    if not wifi_ok:
        print("Cannot start node without network connection.")
        return

    # 2. Setup TCP GridConnect Transport to JMRI or LCC Hub
    print(f"[LCC] Connecting to LCC Hub at {LCC_HUB_IP}:{LCC_HUB_PORT}...")
    transport = GridConnectTcpClient(LCC_HUB_IP, LCC_HUB_PORT)

    # 3. Define Node Identification
    node_id = NodeID("05.01.01.01.14.20")
    snip = SnipData(
        manufacturer="SVL WiFi Nodes",
        model="Pico-W / ESP32 Wireless Node",
        hardware_version="WiFi-v1",
        software_version="0.1.0",
        user_name="WiFi Siding Controller",
        user_description="Wireless OpenLCB Node over TCP",
    )

    # 4. Instantiate Node Orchestrator
    node = LCCNode(
        node_id=node_id,
        transport=transport,
        snip=snip,
        config_file="wifi_node_config.bin",
    )

    # 5. Attach Turnout and Sensor Components
    # Turnout: Siding switch
    turnout = LCCTurnout(
        name="Remote Siding Switch",
        close_event=EventID.from_node_id(node_id, 0x01),
        throw_event=EventID.from_node_id(node_id, 0x02),
        is_closed_event=EventID.from_node_id(node_id, 0x03),
        is_thrown_event=EventID.from_node_id(node_id, 0x04),
        on_change=lambda state: print(f"  [HARDWARE] Physical turnout actuator -> {state}"),
    )
    node.add_component(turnout)

    # Sensor: Siding track occupancy detector
    sensor = LCCSensor(
        name="Siding Detector",
        active_event=EventID.from_node_id(node_id, 0x10),
        inactive_event=EventID.from_node_id(node_id, 0x11),
        on_change=lambda state: print(f"  [DETECTOR] Siding occupancy state -> {state}"),
    )
    node.add_component(sensor)

    # 6. Start Node (allocates CAN alias, sends Init Complete, serves CDI over TCP)
    print("[LCC] Starting OpenLCB Node...")
    await node.start()
    print(f"\n>>> Node Online! Alias: 0x{node.alias:03X} <<<")
    print("Connected wirelessly to JMRI! CDI configuration and events are fully operational.")
    print("Press Ctrl+C to terminate.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        print("\nShutting down wireless node...")
        await node.stop()
        print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
