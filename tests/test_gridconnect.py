import unittest
from pylcc.can import CanFrame
from pylcc.gridconnect import encode_gridconnect, decode_gridconnect, GridConnectParser


class TestGridConnect(unittest.TestCase):
    def test_encode_extended(self):
        frame = CanFrame(can_id=0x195B422A, data=b"\x01\x02\x03\x04\x05\x06\x07\x08")
        gc_str = encode_gridconnect(frame)
        self.assertEqual(gc_str, ":X195B422AN0102030405060708;")

    def test_encode_no_data(self):
        frame = CanFrame(can_id=0x10700123, data=b"")
        gc_str = encode_gridconnect(frame)
        self.assertEqual(gc_str, ":X10700123N;")

    def test_encode_standard(self):
        frame = CanFrame(can_id=0x123, data=b"\xAA\xBB", is_extended=False)
        gc_str = encode_gridconnect(frame)
        self.assertEqual(gc_str, ":S123NAABB;")

    def test_decode_extended(self):
        gc_str = ":X195B422AN0102030405060708;"
        frame = decode_gridconnect(gc_str)
        self.assertEqual(frame.can_id, 0x195B422A)
        self.assertEqual(frame.data, b"\x01\x02\x03\x04\x05\x06\x07\x08")
        self.assertTrue(frame.is_extended)
        self.assertFalse(frame.is_rtr)

    def test_decode_lowercase(self):
        gc_str = ":x195b422an010203;"
        frame = decode_gridconnect(gc_str)
        self.assertEqual(frame.can_id, 0x195B422A)
        self.assertEqual(frame.data, b"\x01\x02\x03")

    def test_decode_standard(self):
        gc_str = ":S123N112233;"
        frame = decode_gridconnect(gc_str)
        self.assertEqual(frame.can_id, 0x123)
        self.assertEqual(frame.data, b"\x11\x22\x33")
        self.assertFalse(frame.is_extended)

    def test_decode_malformed(self):
        # Missing colon or semicolon
        with self.assertRaises(ValueError):
            decode_gridconnect("X195B422AN01;")
        with self.assertRaises(ValueError):
            decode_gridconnect(":X195B422AN01")
        # Odd number of data hex digits
        with self.assertRaises(ValueError):
            decode_gridconnect(":X195B422AN123;")
        # Bad characters in ID
        with self.assertRaises(ValueError):
            decode_gridconnect(":X195Z422AN01;")
        # Exceeds 8 data bytes
        with self.assertRaises(ValueError):
            decode_gridconnect(":X195B422AN010203040506070809;")

    def test_stream_parser_complete(self):
        parser = GridConnectParser()
        frames = parser.feed(":X195B422AN0102; :X10700123N;\n")
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].can_id, 0x195B422A)
        self.assertEqual(frames[0].data, b"\x01\x02")
        self.assertEqual(frames[1].can_id, 0x10700123)
        self.assertEqual(frames[1].data, b"")

    def test_stream_parser_chunked(self):
        parser = GridConnectParser()
        # Feed one byte at a time to test fragmentation resilience
        raw = ":X195B422AN11223344;"
        received = []
        for ch in raw:
            received.extend(parser.feed(ch))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].can_id, 0x195B422A)
        self.assertEqual(received[0].data, b"\x11\x22\x33\x44")

    def test_stream_parser_garbage_recovery(self):
        parser = GridConnectParser()
        # Interleaved junk before valid frame
        data = "some garbage :junk :X195B422ANFF; trailing junk"
        frames = parser.feed(data)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].can_id, 0x195B422A)
        self.assertEqual(frames[0].data, b"\xFF")


if __name__ == "__main__":
    unittest.main()
