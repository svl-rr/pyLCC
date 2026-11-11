import unittest
from pylcc.can import (
    CanFrame,
    OPENLCB_CAN_FRAME_FLAG,
    FRAME_TYPE_MASK,
    CONTROL_RID,
    CONTROL_AMD,
    CONTROL_AME,
    CONTROL_AMR,
)


class TestCanFrame(unittest.TestCase):
    def test_basic_frame(self):
        frame = CanFrame(can_id=0x195B422A, data=b"\x01\x02\x03\x04")
        self.assertEqual(frame.can_id, 0x195B422A)
        self.assertEqual(frame.data, b"\x01\x02\x03\x04")
        self.assertTrue(frame.is_extended)
        self.assertFalse(frame.is_rtr)
        self.assertEqual(frame.source_alias, 0x22A)

    def test_data_length_limit(self):
        # 8 bytes is allowed
        frame = CanFrame(0x100, b"12345678")
        self.assertEqual(len(frame.data), 8)
        # 9 bytes should raise ValueError
        with self.assertRaises(ValueError):
            CanFrame(0x100, b"123456789")

    def test_openlcb_flags(self):
        # Bit 28=1, Bit 27=1 -> OpenLCB message
        msg_frame = CanFrame(0x195B422A)
        self.assertTrue(msg_frame.is_openlcb)
        self.assertTrue(msg_frame.is_openlcb_message)
        self.assertFalse(msg_frame.is_can_control)

        # Bit 28=1, Bit 27=0 -> CAN control frame
        ctrl_frame = CanFrame(0x1070022A)
        self.assertTrue(ctrl_frame.is_openlcb)
        self.assertFalse(ctrl_frame.is_openlcb_message)
        self.assertTrue(ctrl_frame.is_can_control)

    def test_cid_frames(self):
        # Test CID 7 through 4
        alias = 0x3AA
        node_id_chunks = {
            7: 0x012,
            6: 0x345,
            5: 0x678,
            4: 0x9AB,
        }
        for seq, chunk in node_id_chunks.items():
            cid = CanFrame.make_cid(sequence=seq, nid_chunk=chunk, alias=alias)
            self.assertTrue(cid.is_cid, f"Failed for sequence {seq}")
            self.assertEqual(cid.cid_sequence, seq)
            self.assertEqual(cid.cid_chunk, chunk)
            self.assertEqual(cid.source_alias, alias)
            self.assertEqual(len(cid.data), 0)

        # Invalid sequence numbers
        with self.assertRaises(ValueError):
            CanFrame.make_cid(sequence=3, nid_chunk=0, alias=alias)
        with self.assertRaises(ValueError):
            CanFrame.make_cid(sequence=8, nid_chunk=0, alias=alias)

    def test_rid_frame(self):
        alias = 0x555
        rid = CanFrame.make_rid(alias=alias)
        self.assertTrue(rid.is_rid)
        self.assertEqual(rid.source_alias, alias)
        self.assertEqual(rid.variable_field, CONTROL_RID)
        self.assertEqual(len(rid.data), 0)

    def test_amd_frame(self):
        alias = 0x123
        node_id = b"\x05\x01\x01\x01\x14\x00"
        amd = CanFrame.make_amd(alias=alias, node_id_bytes=node_id)
        self.assertTrue(amd.is_amd)
        self.assertEqual(amd.source_alias, alias)
        self.assertEqual(amd.variable_field, CONTROL_AMD)
        self.assertEqual(amd.data, node_id)

        # AMD requires exactly 6 bytes
        with self.assertRaises(ValueError):
            CanFrame.make_amd(alias=alias, node_id_bytes=b"\x01\x02")

    def test_ame_frame(self):
        alias = 0x456
        # Global enquiry
        ame_global = CanFrame.make_ame(alias=alias)
        self.assertTrue(ame_global.is_ame)
        self.assertEqual(ame_global.data, b"")

        # Specific node enquiry
        target_nid = b"\x05\x01\x01\x01\x14\x00"
        ame_targeted = CanFrame.make_ame(alias=alias, target_node_id=target_nid)
        self.assertTrue(ame_targeted.is_ame)
        self.assertEqual(ame_targeted.data, target_nid)

    def test_amr_frame(self):
        alias = 0x789
        node_id = b"\x05\x01\x01\x01\x14\x00"
        amr = CanFrame.make_amr(alias=alias, node_id_bytes=node_id)
        self.assertTrue(amr.is_amr)
        self.assertEqual(amr.source_alias, alias)
        self.assertEqual(amr.variable_field, CONTROL_AMR)
        self.assertEqual(amr.data, node_id)

    def test_equality_and_repr(self):
        f1 = CanFrame(0x195B422A, b"\x01\x02")
        f2 = CanFrame(0x195B422A, b"\x01\x02")
        f3 = CanFrame(0x195B422A, b"\x01\x03")
        self.assertEqual(f1, f2)
        self.assertNotEqual(f1, f3)
        self.assertIn("195B422A", repr(f1))


if __name__ == "__main__":
    unittest.main()
