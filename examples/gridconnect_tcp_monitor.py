"""
OpenLCB Traffic Monitor over GridConnect TCP.
Connects to an LCC TCP Hub (or JMRI) and decodes OpenLCB / CAN frames in real time,
including Alias allocation, Events (PCER), Datagrams, SNIP, and Protocol Support (PIP).
"""

import asyncio
import sys
from pylcc.transport.tcp import GridConnectTcpClient
from pylcc.gridconnect import encode_gridconnect
from pylcc.event_id import EventID


# MTI Mappings for readable diagnostic logging
MTI_NAMES = {
    0x0100: "Initialization Complete (Simple)",
    0x0101: "Initialization Complete (Full)",
    0x04C4: "Consumer Identified (Unknown)",
    0x04C5: "Consumer Identified (Valid)",
    0x04C7: "Consumer Identified (Invalid)",
    0x0544: "Producer Identified (Unknown)",
    0x0545: "Producer Identified (Valid)",
    0x0547: "Producer Identified (Invalid)",
    0x05B4: "Producer/Consumer Event Report (PCER)",
    0x08F4: "Identify Consumer",
    0x0914: "Identify Producer",
    0x0976: "Identify Events (Global)",
    0x0968: "Identify Events (Addressed)",
    0x0828: "Protocol Support Inquiry (PIP)",
    0x0668: "Protocol Support Reply (PIP)",
    0x0DE8: "Simple Node Ident Info Request (SNIP)",
    0x0A08: "Simple Node Ident Info Reply (SNIP)",
    0x0A28: "Datagram Received OK",
    0x0A48: "Datagram Rejected",
}


def format_frame_description(frame) -> str:
    """Decode CAN control frames or OpenLCB message frames into human-readable descriptions."""
    parts = []

    # 1. CAN Control Frames
    if frame.is_cid:
        parts.append(f"CAN CID {frame.cid_sequence} (Chunk=0x{frame.cid_chunk:03X})")
    elif frame.is_rid:
        parts.append("CAN RID (Reserve ID)")
    elif frame.is_amd:
        nid_str = ":".join(f"{b:02X}" for b in frame.data)
        parts.append(f"CAN AMD (Alias Map Def: NodeID={nid_str})")
    elif frame.is_ame:
        parts.append("CAN AME (Alias Map Enquiry)")
    elif frame.is_amr:
        nid_str = ":".join(f"{b:02X}" for b in frame.data)
        parts.append(f"CAN AMR (Alias Map Reset: NodeID={nid_str})")

    # 2. Datagram Frames
    elif frame.is_datagram_only:
        parts.append(f"Datagram ONLY -> Dest=0x{frame.destination_alias:03X}")
    elif frame.is_datagram_first:
        parts.append(f"Datagram FIRST -> Dest=0x{frame.destination_alias:03X}")
    elif frame.is_datagram_middle:
        parts.append(f"Datagram MIDDLE -> Dest=0x{frame.destination_alias:03X}")
    elif frame.is_datagram_final:
        parts.append(f"Datagram FINAL -> Dest=0x{frame.destination_alias:03X}")

    # 3. OpenLCB Message Frames
    elif frame.is_openlcb_message:
        mti = frame.mti
        mti_name = MTI_NAMES.get(mti, f"MTI 0x{mti:04X}")
        parts.append(f"OpenLCB {mti_name}")

        if frame.is_addressed:
            parts.append(f"Dest=0x{frame.destination_alias:03X}")

        # Specialized payload formatting
        if mti == 0x05B4 and len(frame.data) == 8:
            # PCER Event Report
            eid = EventID(frame.data)
            parts.append(f"EventID={eid}")
        elif mti in (0x04C4, 0x04C5, 0x04C7, 0x0544, 0x0545, 0x0547) and len(frame.data) == 8:
            eid = EventID(frame.data)
            parts.append(f"EventID={eid}")
        elif mti == 0x0100 and len(frame.data) >= 6:
            nid = ":".join(f"{b:02X}" for b in frame.data[:6])
            parts.append(f"NodeID={nid}")

    else:
        parts.append(f"CAN Frame (ID=0x{frame.can_id:08X})")

    parts.append(f"Src=0x{frame.source_alias:03X}")
    if frame.data:
        parts.append(f"Data=[{frame.data.hex().upper()}]")

    return " | ".join(parts)


async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 12021

    print(f"Connecting to GridConnect TCP at {host}:{port}...")
    client = GridConnectTcpClient(host=host, port=port)
    await client.connect()
    print("Connected! Monitoring OpenLCB CAN traffic in real time (Press Ctrl+C to stop)...\n")

    def on_frame(frame):
        gc = encode_gridconnect(frame)
        desc = format_frame_description(frame)
        print(f"{gc:<32} {desc}")

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
