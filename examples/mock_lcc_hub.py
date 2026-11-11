"""
Run a lightweight GridConnect TCP Hub (defaults to 127.0.0.1:12021).
Allows multiple nodes or JMRI to connect and exchange OpenLCB frames.
"""

import asyncio
import sys
from pylcc.transport.tcp import GridConnectTcpServer
from pylcc.gridconnect import encode_gridconnect


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021

    print(f"=== pyLCC GridConnect Hub listening on {host}:{port} ===")
    print("Connect JMRI (OpenLCB over TCP) or pyLCC nodes to this endpoint.")
    print("Press Ctrl+C to terminate.\n")

    hub = GridConnectTcpServer(host=host, port=port)

    # Print any frame passing through the hub
    def on_frame(frame):
        gc = encode_gridconnect(frame)
        print(f"[HUB RX] {gc:<32} (ID=0x{frame.can_id:08X}, Alias=0x{frame.source_alias:03X})")

    hub.add_listener(on_frame)
    await hub.start()

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await hub.close()
        print("\nHub stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
