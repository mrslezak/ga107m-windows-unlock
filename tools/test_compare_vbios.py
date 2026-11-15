"""Synthetic, offline-only tests for the ROM inventory tool."""

import unittest

from compare_vbios import RomError, differences, inspect


def put(rom, offset, value, size):
    rom[offset:offset + size] = value.to_bytes(size, "little")


def fixture():
    rom = bytearray(2048)
    rom[:2] = b"\x55\xaa"
    put(rom, 0x18, 0x40, 2)
    rom[0x40:0x44] = b"PCIR"
    put(rom, 0x50, 4, 2)
    rom[0x55] = 0x80
    bit = 0x80
    rom[bit:bit + 6] = b"\xff\xb8BIT\x00"
    rom[bit + 8:bit + 11] = bytes((12, 8, 1))
    rom[bit + 7] = (-sum(rom[bit:bit + 12])) & 0xff
    token = bit + 12
    rom[token:token + 4] = bytes((0x70, 2, 4, 0))
    put(rom, token + 4, 0x100, 4)
    put(rom, 0x100, 0x120, 4)
    rom[0x120:0x124] = bytes((1, 6, 6, 1))
    entry = 0x126
    rom[entry] = 0x85
    put(rom, entry + 2, 0x200, 4)
    desc = 0x200
    put(rom, desc, (428 << 16) | 0x301, 4)
    put(rom, desc + 4, 768, 4)
    put(rom, desc + 8, 0, 4)
    put(rom, desc + 12, 384, 4)
    put(rom, desc + 20, 256, 4)
    put(rom, desc + 32, 512, 4)
    put(rom, desc + 36, 0x400, 2)
    rom[desc + 38:desc + 40] = bytes((1, 1))
    put(rom, desc + 40, 1, 2)
    return rom


class RomTests(unittest.TestCase):
    def test_inventory_locates_rom_resident_fwsec(self):
        summary = inspect(bytes(fixture()))
        self.assertEqual(summary["images"][0]["size"], 2048)
        self.assertEqual(summary["fwsec"]["descriptor"]["offset"], 0x200)
        self.assertEqual(summary["fwsec"]["signatures"]["size"], 384)
        self.assertEqual(summary["fwsec"]["image"]["size"], 768)

    def test_comparison_reports_regions_not_flash_instructions(self):
        original = fixture()
        changed = bytearray(original)
        changed[0x600] = 0x55
        self.assertNotEqual(inspect(original)["fwsec"]["image"]["sha256"],
                            inspect(changed)["fwsec"]["image"]["sha256"])
        self.assertEqual(differences(original, changed)["changed_ranges"],
                         [{"offset": 0x600, "size": 1}])
        self.assertEqual(differences(original, original)["total_changed_ranges"], 0)
        self.assertEqual(differences(original, original + b"X")["changed_ranges"],
                         [{"offset": len(original), "size": 1}])

    def test_rejects_bad_image_and_descriptor_geometry(self):
        rom = fixture()
        rom[0x40:0x44] = b"bad!"
        with self.assertRaises(RomError):
            inspect(rom)
        rom = fixture()
        put(rom, 0x200 + 4, 0x100000, 4)
        with self.assertRaises(RomError):
            inspect(rom)
        with self.assertRaises(RomError):
            inspect(bytes(1024 * 1024 + 1))


if __name__ == "__main__":
    unittest.main()