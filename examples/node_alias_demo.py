"""
Virtual Node Alias Demo.
Connects to an LCC GridConnect Hub (e.g. mock_lcc_hub or JMRI),
negotiates a 12-bit alias via CID/RID/AMD, and defends it.
"""

import asyncio
import sys
from pylcc.node_id import NodeID
from pylcc.alias import AliasManager
from pylcc.transport.tcp import GridConnectTcpClient
from pylcc.gridconnect import encode_gridconnect


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021
    node_id_str = sys.argv[3] if len(sys.argv) > 3 else "05.01.01.01.14.01"

    node_id = NodeID(node_id_str)
    print(f"=== pyLCC Virtual Node: {node_id} ===")
    print(f"Connecting to GridConnect TCP at {host}:{port}...")

    transport = GridConnectTcpClient(host=host, port=port)
    await transport.connect()

    alias_manager = AliasManager(node_id=node_id, transport=transport)

    # Log incoming/outgoing frames
    def on_frame_received(frame):
        gc = encode_gridconnect(frame)
        print(f"  [CAN IN]  {gc:<30} (ID=0x{frame.can_id:08X}, SrcAlias=0x{frame.source_alias:03X})")
        asyncio.create_task(alias_manager.process_frame(frame))

    transport.add_listener(on_frame_received)

    print("\nStarting CAN Alias Allocation (CID 7..4 -> RID -> AMD)...")
    alias = await alias_manager.allocate(check_delay_ms=100)
    print(f"\n>>> SUCCESS: Node {node_id} successfully allocated Alias: 0x{alias:03X} <<<")
    print("Node is now in PERMITTED state. Listening on the bus (Press Ctrl+C to disconnect)...\n")

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        print("\nReleasing alias and shutting down...")
        await alias_manager.release()
        await transport.close()
        print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
