"""
WiFi <-> CAN Hardware Bridge Application.
Bridges physical OpenLCB CAN bus (125 kbps) with wireless TCP clients (e.g. JMRI).
Runs on:
- ESP32 / ESP32-C6 (Native TWAI CAN + on-chip WiFi)
- Raspberry Pi Pico W + MCP2518FD (SPI CAN + on-chip WiFi)
- Desktop Python (simulating a bridge between mock CAN and TCP)
"""

try:
    import asyncio
except ImportError:
    import uasyncio as asyncio

import sys

# Attempt MicroPython imports
try:
    import network
except ImportError:
    network = None

from pylcc import LCCBridge, LCCNode, SnipData, NodeID
from pylcc.transport.tcp import GridConnectTcpServer


# --- Configuration ---
WIFI_SSID = "Your_WiFi_SSID"
WIFI_PASSWORD = "Your_WiFi_Password"
BRIDGE_PORT = 12021

# CAN Pins (Defaults for ESP32 / ESP32-C6)
CAN_TX_PIN = 5
CAN_RX_PIN = 4


async def setup_wifi(ssid: str, password: str) -> bool:
    """Connect to local WiFi or report desktop simulation."""
    if network is None:
        print("[WIFI] Network module not available (running in desktop mode).")
        return True

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print(f"[WIFI] Already connected. IP: {wlan.ifconfig()[0]}")
        return True

    print(f"[WIFI] Connecting to '{ssid}'...")
    wlan.connect(ssid, password)

    for _ in range(30):
        if wlan.isconnected():
            print(f"[WIFI] Connected! Bridge IP Address: {wlan.ifconfig()[0]}")
            return True
        await asyncio.sleep(0.5)

    print("[WIFI] Failed to connect to WiFi.")
    return False


def get_hardware_can_transport():
    """Attempt to detect and initialize the physical CAN controller."""
    # 1. Check for ESP32 native TWAI
    try:
        from pylcc.hardware.esp32_twai import Esp32TwaiTransport
        print(f"[CAN] Initializing ESP32 native TWAI (TX=GPIO{CAN_TX_PIN}, RX=GPIO{CAN_RX_PIN}, 125 kbps)...")
        return Esp32TwaiTransport(tx_pin=CAN_TX_PIN, rx_pin=CAN_RX_PIN, baudrate=125000)
    except Exception:
        pass

    # 2. Check for Pico MCP2518FD SPI
    try:
        from pylcc.hardware.mcp2518fd import Mcp2518fdTransport
        print("[CAN] Initializing Pico MCP2518FD SPI (125 kbps, 31-message FIFO)...")
        return Mcp2518fdTransport(baudrate=125000)
    except Exception:
        pass

    # 3. Fallback for desktop simulation
    from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
    print("[CAN] Physical CAN peripheral not detected. Running with simulated Loopback bus.")
    return LoopbackTransport(LoopbackBus())


async def main():
    print("==================================================")
    print("     pyLCC WiFi <-> CAN Hardware Bridge Gateway   ")
    print("==================================================")

    # 1. Setup WiFi
    wifi_ok = await setup_wifi(WIFI_SSID, WIFI_PASSWORD)
    if not wifi_ok:
        return

    # 2. Initialize Physical CAN Transport
    can_transport = get_hardware_can_transport()

    # 3. Initialize GridConnect TCP Hub Server on port 12021
    print(f"[TCP] Starting GridConnect TCP Hub Server on port {BRIDGE_PORT}...")
    tcp_transport = GridConnectTcpServer(host="0.0.0.0", port=BRIDGE_PORT)

    # 4. Instantiate Bidirectional Bridge
    bridge = LCCBridge(
        can_transport=can_transport,
        tcp_transport=tcp_transport,
        verbose=False,
    )

    # 5. Optionally create a local Node on the bridge itself
    # Allows JMRI to discover the bridge in the OpenLCB Network Tree!
    bridge_node_id = NodeID("05.01.01.01.14.FE")
    snip = SnipData(
        manufacturer="SVL Layout Systems",
        model="pyLCC WiFi-CAN Bridge",
        hardware_version="HW-Bridge-v1",
        software_version="0.1.0",
        user_name="Layout WiFi Gateway",
        user_description="Bidirectional OpenLCB CAN <-> WiFi Bridge",
    )
    # The bridge node listens on the CAN transport so both sides see it
    bridge_node = LCCNode(
        node_id=bridge_node_id,
        transport=can_transport,
        snip=snip,
        config_file="bridge_node_config.bin",
    )

    # Start bridge and bridge node
    print("\nActivating bridge...")
    await bridge.start()
    await bridge_node.start()

    print(f"\n>>> BRIDGE ACTIVE & RUNNING! <<<")
    print(f"• Physical CAN: 125 kbps (OpenLCB standard)")
    print(f"• TCP Server  : Listening on port {BRIDGE_PORT} (Connect JMRI here)")
    print(f"• Gateway Node: {bridge_node_id} (Alias: 0x{bridge_node.alias:03X})")
    print("Press Ctrl+C to terminate.\n")

    # Periodic statistics monitor loop
    try:
        while True:
            await asyncio.sleep(5)
            stats = bridge.get_stats()
            print(
                f"[BRIDGE STATS] CAN -> WiFi: {stats['can_to_tcp']:<6} | "
                f"WiFi -> CAN: {stats['tcp_to_can']:<6} | "
                f"Errors (CAN/TCP): {stats['can_tx_errors']}/{stats['tcp_tx_errors']}"
            )
    except asyncio.CancelledError:
        pass
    finally:
        print("\nShutting down bridge gateway...")
        await bridge_node.stop()
        await bridge.stop()
        print("Bridge stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
