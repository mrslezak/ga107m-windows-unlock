"""Synthetic, offline-only tests for the ROM selector-reference scanner."""

import unittest

from compare_vbios import RomError
from test_compare_vbios import fixture, put
from vbios_devinit import analyze


FREE = 0x700          # past the fixture FWSEC image, still inside image[0]
SIGNED_IMAGE = 0x400  # inside the fixture FWSEC image
SIGNED_BLOCK = 0x300  # inside the fixture FWSEC signature block


class SelectorScanTests(unittest.TestCase):
    def test_clean_rom_reports_no_selector_reference(self):
        report = analyze(bytes(fixture()))
        self.assertEqual(report["summary"]["references"], 0)
        self.assertIn("No ROM-resident reference", report["verdict"])

    def test_reports_bit_tokens_and_absent_init_token(self):
        report = analyze(bytes(fixture()))
        self.assertEqual([entry["name"] for entry in report["bit"]["entries"]], ["p"])
        self.assertFalse(report["summary"]["init_token_present"])

    def test_unsigned_reference_is_an_editable_candidate(self):
        rom = fixture()
        put(rom, FREE, 0x0082381c, 4)
        put(rom, FREE + 10, 0x00823820, 4)
        report = analyze(bytes(rom))
        self.assertEqual(report["summary"]["editable_candidates"], 2)
        first, second = report["references"]
        self.assertEqual((first["register"], first["offset"], first["aligned"]),
                         ("ss0", FREE, True))
        self.assertEqual((second["register"], second["aligned"]), ("ss1", False))
        self.assertEqual(first["image"], "image[0]")
        self.assertEqual(first["signed_region"], "")
        self.assertEqual(first["context_offset"], FREE - 8)
        self.assertIn("outside parsed FWSEC structures", report["verdict"])

    def test_reference_inside_signed_regions_is_not_editable(self):
        rom = fixture()
        put(rom, SIGNED_IMAGE, 0x0082381c, 4)
        put(rom, SIGNED_BLOCK, 0x00823804, 4)
        report = analyze(bytes(rom))
        self.assertEqual(report["summary"]["editable_candidates"], 0)
        self.assertEqual(report["summary"]["signed_references"], 2)
        regions = {hit["offset"]: hit["signed_region"] for hit in report["references"]}
        self.assertEqual(regions[SIGNED_IMAGE], "fwsec_image")
        self.assertEqual(regions[SIGNED_BLOCK], "fwsec_signatures")
        self.assertIn("have not been verified", report["verdict"])

    def test_custom_window_matches_unnamed_register(self):
        rom = fixture()
        put(rom, FREE, 0x00999004, 4)
        self.assertEqual(analyze(bytes(rom))["summary"]["references"], 0)
        report = analyze(bytes(rom), base=0x00999000, size=0x10)
        self.assertEqual(report["summary"]["references"], 1)
        self.assertEqual(report["references"][0]["register"], "")
        self.assertEqual(report["window"], {"base": "0x00999000", "size": 0x10})

    def test_rejects_unusable_window_and_context(self):
        rom = bytes(fixture())
        for keywords in ({"base": 0x00823802}, {"size": 2}, {"size": 0x20000},
                         {"base": 0xfffffff0, "size": 0x20}, {"context": 100}):
            with self.assertRaises(RomError):
                analyze(rom, **keywords)


if __name__ == "__main__":
    unittest.main()
