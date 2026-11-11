import asyncio
import unittest
from pylcc.can import CanFrame
from pylcc.transport.loopback import LoopbackBus, LoopbackTransport
from pylcc.transport.tcp import GridConnectTcpClient, GridConnectTcpServer


class TestTransport(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_transport(self):
        bus = LoopbackBus()
        t1 = LoopbackTransport(bus)
        t2 = LoopbackTransport(bus)

        frame_to_send = CanFrame(0x195B422A, b"\x01\x02\x03\x04")

        # t1 sends, t2 should receive
        await t1.send_frame(frame_to_send)
        received = await asyncio.wait_for(t2.receive_frame(), timeout=1.0)
        self.assertEqual(received, frame_to_send)

        # t2 sends, t1 should receive
        reply_frame = CanFrame.make_rid(0x22A)
        await t2.send_frame(reply_frame)
        received_reply = await asyncio.wait_for(t1.receive_frame(), timeout=1.0)
        self.assertEqual(received_reply, reply_frame)

        await t1.close()
        await t2.close()

    async def test_tcp_gridconnect_roundtrip(self):
        # Pick an arbitrary high port
        test_port = 12055
        server = GridConnectTcpServer(host="127.0.0.1", port=test_port)
        await server.start()

        client = GridConnectTcpClient(host="127.0.0.1", port=test_port)
        await client.connect()

        # Give connection a moment to initialize
        await asyncio.sleep(0.05)

        # Client -> Server
        client_frame = CanFrame(0x195B422A, b"\xCA\xFE")
        await client.send_frame(client_frame)

        server_rx = await asyncio.wait_for(server.receive_frame(), timeout=2.0)
        self.assertEqual(server_rx, client_frame)

        # Server -> Client
        server_frame = CanFrame.make_amd(0x123, b"\x05\x01\x01\x01\x14\x00")
        await server.send_frame(server_frame)

        client_rx = await asyncio.wait_for(client.receive_frame(), timeout=2.0)
        self.assertEqual(client_rx, server_frame)

        await client.close()
        await server.close()


if __name__ == "__main__":
    unittest.main()
