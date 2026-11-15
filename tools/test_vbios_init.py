"""Synthetic offline tests for initialization decoding; never accesses GPU hardware."""

from contextlib import redirect_stdout, redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from compare_vbios import RomError
from test_compare_vbios import fixture, put
from vbios_init import decode, main


def bios(script=b"\x71"):
    data = fixture() + bytearray(6144)
    put(data, 0x50, 16, 2)
    data[0x8a] = 2  # retain the existing p token, add I
    data[0x94:0x98] = bytes((ord("I"), 1, 36, 0))
    put(data, 0x98, 0x700, 4)
    data[0x87] = 0
    data[0x87] = (-sum(data[0x80:0x8c])) & 255
    put(data, 0x700, 0x740, 2)
    put(data, 0x704, 0x780, 2)
    put(data, 0x706, 0x7a0, 2)
    put(data, 0x740, 0x800, 2)
    data[0x800:0x800 + len(script)] = script
    return data


def instruction(opcode, *words):
    return bytes((opcode,)) + b"".join(word.to_bytes(4, "little") for word in words)


class InitTests(unittest.TestCase):
    def test_discovers_init_and_stops_at_done_without_scanning_garbage(self):
        report = decode(bios(b"\x71\xff"))
        self.assertEqual(report["summary"]["roots"], 1)
        self.assertEqual(report["summary"]["instructions"], 1)
        self.assertFalse(report["issues"])
        self.assertFalse(report["summary"]["patch_eligible"])
        self.assertFalse(report["coverage"]["complete_gpu_initialization"])

    def test_direct_and_masked_selector_writes_have_value_locations(self):
        script = instruction(0x7a, 0x82381c, 0x88888888)
        script += instruction(0x6e, 0x823820, 0xfffffff0, 8) + b"\x71"
        report = decode(bios(script))
        writes = report["selector_operations"]
        self.assertEqual([w["register"] for w in writes], ["ss0", "ss1"])
        self.assertEqual(writes[0]["value"], 0x88888888)
        self.assertEqual(writes[0]["value_location"]["file_offset"], 0x805)
        self.assertEqual(writes[1]["and_mask"], 0xfffffff0)
        self.assertEqual(writes[0]["authentication"], "unknown")

    def test_sequence_detects_computed_selector_addresses(self):
        # SS0/SS1 do not occur literally as address operands here.
        script = instruction(0x58, 0x823818) + bytes((3,))
        script += b"".join(n.to_bytes(4, "little") for n in (0, 0x88888888, 8))
        report = decode(bios(script + b"\x71"))
        self.assertEqual([w["register"] for w in report["selector_operations"]], ["", "ss0", "ss1"])
        self.assertEqual(report["summary"]["write_records"], 3)

    def test_group_repeats_same_destination_and_handles_zero_count(self):
        script = instruction(0x91, 0x82381c) + b"\x02" + bytes(8)
        script += instruction(0x58, 0x82381c) + b"\x00\x71"
        report = decode(bios(script))
        self.assertEqual(report["summary"]["write_records"], 2)
        self.assertEqual(report["summary"]["instructions"], 3)

    def test_macro_and_condition_follow_tables_without_evaluating(self):
        data = bios(b"\x75\x00\x6f\x00\x71")
        put(data, 0x780, 0x82381c, 4)
        put(data, 0x784, 0x88888888, 4)
        put(data, 0x7a0, 0x823804, 4)
        report = decode(data)
        self.assertEqual(report["selector_operations"][0]["kind"], "macro constant")
        self.assertEqual(report["selector_operations"][0]["value_location"]["logical_offset"], 0x784)
        self.assertEqual(report["instructions"][0]["condition"]["result"], "unknown")

    def test_calls_jumps_cycles_and_fallthrough_are_bounded(self):
        data = bios(b"\x5b\x00\x09\x5c\x00\x08\x71")
        data[0x900:0x903] = b"\x6b\x00\x71"
        report = decode(data)
        self.assertFalse(report["issues"])
        self.assertEqual(report["summary"]["instructions"], 5)
        self.assertEqual(len(report["instructions"][1]["successors"]), 2)

    def test_repeat_is_decoded_once_not_executed(self):
        script = b"\x33\xff" + instruction(0x7a, 0x82381c, 1) + b"\x36\x71"
        report = decode(bios(script))
        self.assertEqual(report["summary"]["instructions"], 4)
        self.assertEqual(report["summary"]["write_records"], 1)

    def test_unknown_opcode_stops_without_resynchronization(self):
        data = bios(b"\xff" + instruction(0x7a, 0x82381c, 1) + b"\x71")
        report = decode(data)
        self.assertEqual(report["summary"]["instructions"], 0)
        self.assertEqual(report["summary"]["selector_operations"], 0)
        self.assertIn("unsupported opcode", report["issues"][0]["reason"])
        self.assertEqual(report["coverage"]["legacy_graph"], "partial")

    def test_truncated_instruction_and_bad_subtable_report_issues(self):
        data = bios()
        put(data, 0x740, len(data) - 2, 2)
        data[-2:] = b"\x7a\x01"
        self.assertIn("outside supplied", decode(data)["issues"][0]["reason"])
        data = bios(b"\x6f\xff\x71")
        put(data, 0x704, len(data) - 1, 2)
        self.assertTrue(decode(data)["issues"])

    def test_control_flow_into_operand_is_reported(self):
        data = bios(b"\x5c\x01\x08\x71")
        report = decode(data)
        self.assertTrue(any("operand" in issue["reason"] for issue in report["issues"]))

    def test_budget_and_root_limits(self):
        data = bios(b"\x72" * 10 + b"\x71")
        report = decode(data, max_instructions=3)
        self.assertEqual(report["summary"]["instructions"], 3)
        self.assertIn("budget", report["issues"][0]["reason"])
        for budget in (0, 16385):
            with self.assertRaises(RomError):
                decode(data, max_instructions=budget)
        for root in (0, -1, 0x10000):
            with self.assertRaises(RomError):
                decode(data, scripts=[root])
        with self.assertRaises(RomError):
            decode(data, scripts=[0x800] * 257)

    def test_unterminated_script_table_fails_closed(self):
        data = bios()
        data[0x740:0x740 + 514] = b"\x00\x08" * 257
        with self.assertRaisesRegex(RomError, "lacks terminator"):
            decode(data)

    def test_invalid_init_metadata_rejected(self):
        for field, value, width in ((0x95, 2, 1), (0x96, 12, 2),
                                    (0x98, 0xffff, 4), (0x700, 0xffff, 2),
                                    (0x714, 0x900, 2)):
            data = bios()
            put(data, field, value, width)
            with self.subTest(field=field), self.assertRaises(RomError):
                decode(data)

    def test_empty_legacy_table_does_not_decode_modern_blobs_or_unknown_tail(self):
        data = bios()
        put(data, 0x740, 0, 2)
        put(data, 0x714, 0x900, 2)
        put(data, 0x716, 32, 2)
        data[0x900:0x909] = instruction(0x7a, 0x82381c, 0x88888888)
        put(data, 0x71c, 0x900, 2)
        report = decode(data)
        self.assertEqual(report["summary"]["instructions"], 0)
        self.assertEqual(report["bit_init"]["blobs"]["pmu_tables"]["size"], 32)
        self.assertTrue(report["bit_init"]["uninterpreted_tail"])
        self.assertFalse(report["summary"]["patch_eligible"])

    def test_no_init_and_manual_root_are_distinguished(self):
        self.assertIsNone(decode(bytes(fixture()))["bit_init"])
        data = fixture()
        data[0x700] = 0x71
        report = decode(data, scripts=[0x700])
        self.assertIn("unproven", report["roots"][0]["source"])

    def test_context_addresses_remain_symbolic(self):
        report = decode(bios(instruction(0x7a, 0x8082381c, 1) + b"\x71"))
        self.assertEqual(report["summary"]["selector_operations"], 0)
        self.assertEqual(report["summary"]["unresolved_addresses"], 1)
        self.assertIsNone(report["unresolved_address_operations"][0]["selector_window_match"])

    def test_io_selected_writes_report_all_possibilities_not_selected_value(self):
        script = b"\x32\xd4\x03\x01\x03\x00\x02"
        script += (0x82381c).to_bytes(4, "little")
        script += (1).to_bytes(4, "little") + (2).to_bytes(4, "little") + b"\x71"
        report = decode(bios(script))
        self.assertEqual([w["value"] for w in report["selector_operations"]], [1, 2])
        self.assertEqual(report["instructions"][0]["io_selection"]["port"], 0x3d4)
        self.assertEqual(report["instructions"][0]["io_selection"]["selected_index"], "requires hardware read")

    def test_copy_translation_mask_and_16bit_value_opcodes(self):
        script = instruction(0x90, 0x100, 0x82381c)
        script += bytes((0x5f,)) + (0x100).to_bytes(4, "little") + bytes((0,))
        script += b"".join(w.to_bytes(4, "little") for w in (0xff, 0, 0x823820, 0))
        script += instruction(0x96, 0x100) + bytes((0, 0xff, 0))
        script += (0x82381c).to_bytes(4, "little") + bytes(5)
        script += instruction(0x97, 0x82381c, 0xffffff00, 1)
        script += instruction(0x47, 0x82381c, 1) + instruction(0x48, 0x82381c, 2)
        script += instruction(0x77, 0x823820) + (8).to_bytes(2, "little") + b"\x71"
        report = decode(bios(script))
        self.assertFalse(report["issues"])
        self.assertEqual(report["summary"]["write_records"], 7)
        self.assertEqual(report["selector_operations"][0]["source_address"], "0x00000100")
        self.assertEqual(report["selector_operations"][2]["kind"], "runtime table translation")
        self.assertEqual(report["selector_operations"][-1]["value"], 8)

    def test_port_operations_are_framed_not_mislabeled_as_mmio(self):
        script = b"\x4c\x00\x00\x01\x01\xff\x02"
        script += b"\x4d\x00\x00\x01\x01\x02"
        script += b"\x4e\x00\x00\x02\x01\x02\x54\x01\x00\x00\x71"
        report = decode(bios(script))
        self.assertFalse(report["issues"])
        self.assertEqual(report["summary"]["instructions"], 5)
        self.assertEqual(report["summary"]["write_records"], 0)
        self.assertIn("not enumerated", report["coverage"]["write_records"])

    def test_sequence_address_overflow_and_code_boundary_are_reported(self):
        script = instruction(0x58, 0xfffffffc) + b"\x02" + bytes(8) + b"\x71"
        self.assertIn("overflows", decode(bios(script))["issues"][0]["reason"])
        data = bios() + bytearray(65536 - 8192)
        put(data, 0x50, 128, 2)
        put(data, 0x740, 0xffff, 2)
        data[0xffff] = 0x7a
        self.assertIn("boundary", decode(data)["issues"][0]["reason"])

    def test_pcie_root_is_decoded_nvlink_is_not_a_script(self):
        data = bios()
        put(data, 0x712, 0x900, 2)
        put(data, 0x71c, 0x910, 2)
        data[0x900] = 0x71
        data[0x910] = 0xff
        report = decode(data)
        self.assertEqual(report["summary"]["roots"], 2)
        self.assertEqual(report["summary"]["instructions"], 2)
        self.assertEqual(report["bit_init"]["pointers"]["nvlink_configuration"], 0x910)
        self.assertFalse(report["issues"])

    def test_nvgi_mapping_and_original_hash_preserved(self):
        raw = bytes(bios(instruction(0x7a, 0x82381c, 1) + b"\x71"))
        data = bytearray(0x1000) + raw
        data[:4] = b"NVGI"
        data[0x100:0x104] = b"RFRD"
        put(data, 0x104, 3, 2)
        put(data, 0x106, 40, 2)
        put(data, 0x108, 0x1000, 4)
        put(data, 0x10c, len(raw), 4)
        before = bytes(data)
        report = decode(data)
        self.assertEqual(report["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(report["selector_operations"][0]["file_offset"], 0x1800)
        self.assertEqual(report["selector_operations"][0]["value_location"]["file_offset"], 0x1805)
        self.assertEqual(bytes(data), before)

    def test_cli_prints_json_only_never_creates_unlocked_rom(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "original.rom"
            original = bytes(bios())
            path.write_bytes(original)
            output = io.StringIO()
            with redirect_stdout(output):
                main([str(path)])
            self.assertFalse(json.loads(output.getvalue())["summary"]["patch_eligible"])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.iterdir()), [path])
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main([str(path), "--max-instructions", "0"])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()