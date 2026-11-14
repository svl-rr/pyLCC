"""
Unit tests for GridConnect USB / UART Serial Transport (pylcc.transport.serial).
Tests both MicroPython-style (any()/read()) and pyserial-style (in_waiting/read()) interfaces.
"""

import asyncio
import unittest
from unittest.mock import patch, MagicMock

from pylcc import LCCNode, LCCSensor, EventID
from pylcc.can import CanFrame
from pylcc.transport import GridConnectSerialTransport


class PairedMockUart:
    """In-memory paired byte stream simulating a crossed TX/RX UART or USB CDC pair."""

    def __init__(self, mode: str = "any"):
        self._rx_buf = bytearray()
        self.peer = None
        self.closed = False
        self.mode = mode  # "any" (MicroPython UART) or "in_waiting" (pyserial)

    @staticmethod
    def create_pair(mode_a: str = "any", mode_b: str = "in_waiting"):
        a = PairedMockUart(mode=mode_a)
        b = PairedMockUart(mode=mode_b)
        a.peer = b
        b.peer = a
        return a, b

    @property
    def in_waiting(self) -> int:
        if self.mode != "in_waiting":
            raise AttributeError("in_waiting")
        return len(self._rx_buf)

    def any(self) -> int:
        if self.mode != "any":
            raise AttributeError("any")
        return len(self._rx_buf)

    def write(self, data: bytes) -> int:
        if self.closed:
            raise OSError("UART closed")
        if self.peer is not None and not self.peer.closed:
            self.peer._rx_buf.extend(data)
        return len(data)

    def read(self, n: int = -1):
        if not self._rx_buf:
            return b"" if self.mode == "in_waiting" else None
        if n is None or n < 0 or n >= len(self._rx_buf):
            out = bytes(self._rx_buf)
            self._rx_buf.clear()
            return out
        out = bytes(self._rx_buf[:n])
        del self._rx_buf[:n]
        return out

    def close(self):
        self.closed = True


class InWaitingOnlyUart:
    """Clean pyserial-style mock (exposes only in_waiting, read, write, close)."""

    def __init__(self):
        self._rx_buf = bytearray()
        self.peer = None
        self.closed = False

    @property
    def in_waiting(self) -> int:
        return len(self._rx_buf)

    def write(self, data: bytes) -> int:
        if self.peer is not None:
            self.peer._rx_buf.extend(data)
        return len(data)

    def read(self, n: int = -1) -> bytes:
        if not self._rx_buf:
            return b""
        take = len(self._rx_buf) if (n is None or n < 0) else min(n, len(self._rx_buf))
        out = bytes(self._rx_buf[:take])
        del self._rx_buf[:take]
        return out

    def close(self):
        self.closed = True


class TestGridConnectSerialTransport(unittest.IsolatedAsyncioTestCase):
    async def test_bidirectional_serial_roundtrip(self):
        uart_mpy = PairedMockUart(mode="any")
        uart_pc = InWaitingOnlyUart()
        uart_mpy.peer = uart_pc
        uart_pc.peer = uart_mpy

        t_mpy = GridConnectSerialTransport(uart=uart_mpy, poll_interval_ms=2)
        t_pc = GridConnectSerialTransport(uart=uart_pc, poll_interval_ms=2)

        await t_mpy.connect()
        await t_pc.connect()

        self.assertTrue(t_mpy.is_connected)
        self.assertTrue(t_pc.is_connected)

        f1 = CanFrame(0x195B422A, b"\x01\x02\x03\x04")
        await t_mpy.send_frame(f1)
        rx1 = await asyncio.wait_for(t_pc.receive_frame(), timeout=1.0)
        self.assertEqual(rx1, f1)

        f2 = CanFrame.make_amd(0x333, b"\x05\x01\x01\x01\x14\x00")
        await t_pc.send_frame(f2)
        rx2 = await asyncio.wait_for(t_mpy.receive_frame(), timeout=1.0)
        self.assertEqual(rx2, f2)

        await t_mpy.close()
        await t_pc.close()
        self.assertFalse(t_mpy.is_connected)
        self.assertTrue(uart_mpy.closed)
        self.assertTrue(uart_pc.closed)

    async def test_fragmented_poll_and_node_exchange(self):
        uart_a = PairedMockUart(mode="any")
        uart_b = InWaitingOnlyUart()
        uart_a.peer = uart_b
        uart_b.peer = uart_a

        ta = GridConnectSerialTransport(uart=uart_a, poll_interval_ms=2)
        tb = GridConnectSerialTransport(uart=uart_b, poll_interval_ms=2)

        # Fragmented injection before connect/poll
        received = []
        tb.add_listener(lambda f: received.append(f))
        tb._is_closed = False
        uart_b._rx_buf.extend(b":X195B4111N")
        self.assertEqual(tb.poll(), 0)
        uart_b._rx_buf.extend(b"AABBCCDD;\r\n")
        self.assertEqual(tb.poll(), 1)
        self.assertEqual(received[0].data, b"\xAA\xBB\xCC\xDD")
        received.clear()

        # Full two-node alias + PCER exchange over serial transport
        node_a = LCCNode("05.01.01.01.14.10", ta)
        node_b = LCCNode("05.01.01.01.14.20", tb)
        seen_events = []
        sensor = LCCSensor(
            name="Serial Sensor",
            active_event="05.01.01.01.14.10.00.01",
            inactive_event="05.01.01.01.14.10.00.02",
        )
        node_a.add_component(sensor)
        node_b.register_consumer("05.01.01.01.14.10.00.01", lambda eid: seen_events.append(eid))

        await node_a.start(check_delay_ms=10)
        await node_b.start(check_delay_ms=10)

        await sensor.set_active(True)
        await asyncio.sleep(0.05)

        self.assertEqual(seen_events, [EventID("05.01.01.01.14.10.00.01")])

        await node_a.stop()
        await node_b.stop()

    async def test_send_when_closed_raises(self):
        t = GridConnectSerialTransport(uart=InWaitingOnlyUart())
        with self.assertRaises(RuntimeError):
            await t.send_frame(CanFrame.make_rid(0x123))

        await t.connect()
        await t.close()
        with self.assertRaises(RuntimeError):
            await t.send_frame(CanFrame.make_rid(0x123))

    async def test_auto_open_micropython_uart_or_pyserial(self):
        # Verify machine.UART path for integer port
        fake_uart = InWaitingOnlyUart()
        fake_machine = MagicMock()
        fake_machine.UART.return_value = fake_uart

        with patch.dict("sys.modules", {"machine": fake_machine}):
            t = GridConnectSerialTransport(port=1, baudrate=230400, tx=4, rx=5)
            await t.connect()
            fake_machine.UART.assert_called_once_with(1, baudrate=230400, tx=4, rx=5)
            await t.close()
            self.assertTrue(fake_uart.closed)

        # Verify pyserial path for device string port
        fake_ser = InWaitingOnlyUart()
        fake_serial_mod = MagicMock()
        fake_serial_mod.Serial.return_value = fake_ser

        with patch.dict("sys.modules", {"serial": fake_serial_mod}):
            t2 = GridConnectSerialTransport(port="/dev/ttyUSB0", baudrate=115200)
            await t2.connect()
            fake_serial_mod.Serial.assert_called_once_with("/dev/ttyUSB0", baudrate=115200, timeout=0)
            await t2.close()
            self.assertTrue(fake_ser.closed)


if __name__ == "__main__":
    unittest.main()
