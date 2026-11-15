"""Synthetic NVGI/RFRD analysis tests; no proprietary ROM fixture or GPU access."""

import hashlib
from pathlib import Path
import tempfile
import unittest

from compare_vbios import RomError, analysis_view, inspect
from test_compare_vbios import fixture, put
from vbios_devinit import analyze
from vbios_patch import generate
from vbios_vault import store, verify


def container():
    raw = fixture()
    data = bytearray(0x1000 + len(raw) + 32)
    data[:4] = b"NVGI"
    data[0x100:0x104] = b"RFRD"
    put(data, 0x104, 3, 2)
    put(data, 0x106, 40, 2)
    put(data, 0x108, 0x1000, 4)
    put(data, 0x10c, len(raw), 4)
    data[0x1000:0x1000 + len(raw)] = raw
    return data


class NvflashTests(unittest.TestCase):
    def test_inventory_preserves_full_file_and_maps_fwsec(self):
        data = bytes(container())
        report = inspect(data)
        self.assertEqual(report["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(report["input_format"], "nvgi-rfrd-v3")
        self.assertEqual(report["images"][0]["offset"], 0x1000)
        self.assertEqual(report["images"][0]["logical_offset"], 0)
        self.assertEqual(report["fwsec"]["descriptor"]["offset"], 0x1200)
        self.assertEqual(report["fwsec"]["descriptor"]["logical_offset"], 0x200)
        self.assertEqual(report["fwsec"]["bit_offset"], 0x1080)
        self.assertEqual(analysis_view(data)[0], bytes(fixture()))

    def test_scan_classifies_wrapper_and_payload_as_unknown_editability(self):
        data = container()
        put(data, 0x200, 0x0082381c, 4)
        put(data, 0x1700, 0x00823820, 4)
        report = analyze(data)
        self.assertEqual(report["summary"]["references"], 2)
        self.assertEqual(report["summary"]["editable_candidates"], 0)
        self.assertEqual(report["summary"]["signed_references"], 0)
        self.assertIsNone(report["references"][0]["logical_offset"])
        self.assertEqual(report["references"][1]["logical_offset"], 0x700)
        self.assertEqual(report["bit"]["offset"], 0x1080)
        self.assertEqual(report["bit"]["entries"][0]["offset"], 0x108c)

    def test_missing_fwsec_is_explicit(self):
        data = container()
        data[0x1080] = 0
        self.assertIsNone(inspect(data)["fwsec"])
        self.assertFalse(analyze(data)["signed_regions"])

    def test_rejects_truncated_ranges_unsupported_headers_and_decoys(self):
        for offset, value, size in ((0x104, 4, 2), (0x106, 8, 2),
                                    (0x108, 0xffffff00, 4), (0x10c, 0, 4),
                                    (0x10c, 4096, 4), (0x108, 0x100, 4)):
            data = container()
            put(data, offset, value, size)
            with self.subTest(offset=offset, value=value), self.assertRaises(RomError):
                inspect(data)
        with self.assertRaises(RomError):
            inspect(bytes(container())[:0x1100])
        data = container()
        data[0x800:0x804] = b"RFRD"
        self.assertEqual(inspect(data)["input_format"], "nvgi-rfrd-v3")

    def test_rejects_ambiguous_records(self):
        data = container()
        data[0x200:0x228] = data[0x100:0x128]
        with self.assertRaises(RomError):
            inspect(data)

    def test_no_hits_is_not_proof_of_no_execution_path(self):
        report = analyze(bytes(container()))
        self.assertIn("does not rule out", report["verdict"])

    def test_vault_keeps_full_container_not_extracted_view(self):
        data = bytes(container())
        expected = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            captures = [root / "a.rom", root / "b.rom"]
            for path in captures:
                path.write_bytes(data)
            manifest = store(root / "vault", captures, expected, 2)
            for entry in manifest["copies"]:
                self.assertEqual((root / "vault" / entry["name"]).read_bytes(), data)
            self.assertEqual(verify(root / "vault", expected)["rom_sha256"], expected)

    def test_patch_generator_rejects_analysis_only_container(self):
        with self.assertRaisesRegex(RomError, "read-only analysis only"):
            generate(bytes(container()), Path("not-used"), None, [])


if __name__ == "__main__":
    unittest.main()