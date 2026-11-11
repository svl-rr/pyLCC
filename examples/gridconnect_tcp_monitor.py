"""
OpenLCB Frame Monitor over GridConnect TCP.
Connects to an LCC TCP Hub (or JMRI) and prints decoded OpenLCB frames in real time.
"""

import asyncio
import sys
from pylcc.transport.tcp import GridConnectTcpClient
from pylcc.gridconnect import encode_gridconnect


def format_frame_description(frame) -> str:
    parts = []
    if frame.is_cid:
        parts.append(f"CID {frame.cid_sequence} (Chunk=0x{frame.cid_chunk:03X})")
    elif frame.is_rid:
        parts.append("RID (Reserve ID)")
    elif frame.is_amd:
        nid_str = ":".join(f"{b:02X}" for b in frame.data)
        parts.append(f"AMD (Alias Map Definition, NodeID={nid_str})")
    elif frame.is_ame:
        parts.append("AME (Alias Map Enquiry)")
    elif frame.is_amr:
        nid_str = ":".join(f"{b:02X}" for b in frame.data)
        parts.append(f"AMR (Alias Map Reset, NodeID={nid_str})")
    elif frame.is_openlcb_message:
        parts.append(f"OpenLCB Message (VarField=0x{frame.variable_field:04X})")
    else:
        parts.append(f"CAN Frame (ID=0x{frame.can_id:08X})")

    parts.append(f"SrcAlias=0x{frame.source_alias:03X}")
    if frame.data:
        parts.append(f"Data=[{frame.data.hex().upper()}]")
    return " | ".join(parts)


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021

    print(f"Connecting to GridConnect TCP at {host}:{port}...")
    client = GridConnectTcpClient(host=host, port=port)
    await client.connect()
    print("Connected! Listening for OpenLCB CAN frames (Press Ctrl+C to stop)...\n")

    def on_frame(frame):
        gc = encode_gridconnect(frame)
        desc = format_frame_description(frame)
        print(f"{gc:<30} -> {desc}")

    client.add_listener(on_frame)

    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await client.close()
        print("\nDisconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
