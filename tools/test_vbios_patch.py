"""Offline tests for the gated patch generator; no GPU, no flashing, synthetic ROMs only."""

import tempfile
import unittest
from pathlib import Path

from compare_vbios import RomError, inspect
from test_compare_vbios import fixture, put
from vbios_devinit import analyze
from vbios_patch import generate, main, parse_edit
from vbios_vault import digest, store


FREE = 0x700
SS0 = 0x0082381c
SS0_BYTES = "1c388200"
NEUTRAL = "00000000"
SIGNED_BLOCK = 0x300
FWSEC_IMAGE_END = 0x6AC


def selector_rom():
    """The fixture plus one unsigned selector literal and a zero image checksum."""
    rom = fixture()
    put(rom, FREE, SS0, 4)
    rom[-1] = (-sum(rom[:-1])) & 0xFF
    return rom


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.rom = bytes(selector_rom())
        self.sha = digest(self.rom)
        self.vault = self.keep(self.rom, "vault")
        self.edit = f"0x{FREE:x}={SS0_BYTES}:{NEUTRAL}"

    def keep(self, data, name):
        captures = []
        for index in range(2):
            path = self.root / f"{name}-{index}.bin"
            path.write_bytes(data)
            captures.append(path)
        vault = self.root / name
        store(vault, captures, digest(data), 2)
        return vault

    def patch(self, *specs, **kwargs):
        return generate(self.rom, self.vault, self.sha, list(specs) or [self.edit], **kwargs)

    def test_generates_a_patched_image_and_keeps_the_signed_bytes(self):
        image, report = self.patch()
        self.assertEqual(len(image), len(self.rom))
        self.assertNotEqual(report["patched"]["sha256"], report["original"]["sha256"])
        self.assertEqual(image[FREE:FREE + 4], bytes.fromhex(NEUTRAL))
        self.assertTrue(report["edits"][0]["selector_reference"])
        original, patched = inspect(self.rom)["fwsec"], inspect(image)["fwsec"]
        for key in ("descriptor", "image", "signatures"):
            self.assertEqual(original[key]["sha256"], patched[key]["sha256"])
        self.assertEqual(analyze(image)["summary"]["editable_candidates"], 0)

    def test_restores_the_checksum_the_original_held(self):
        image, report = self.patch()
        record = report["checksums"][0]
        self.assertEqual(record["checksum_before"], 0)
        self.assertEqual(record["checksum_after"], 0)
        self.assertTrue(record["repaired"])
        self.assertEqual(record["pad_offset"], len(self.rom) - 1)
        self.assertEqual(sum(image) & 0xFF, 0)
        self.assertEqual([span["offset"] for span in report["diff"]["changed_ranges"]],
                         [FREE, len(self.rom) - 1])

    def test_leaves_a_checksum_the_original_never_held(self):
        rom = bytearray(self.rom)
        rom[-1] = (rom[-1] + 1) & 0xFF
        self.rom = bytes(rom)
        self.sha = digest(self.rom)
        self.vault = self.keep(self.rom, "shifted")
        _, report = self.patch()
        record = report["checksums"][0]
        self.assertFalse(record["repaired"])
        self.assertIn("did not sum to zero", record["note"])

    def test_refuses_edits_that_reach_signed_or_absent_bytes(self):
        for spec in (f"0x{SIGNED_BLOCK:x}=00000000:11111111",
                     f"0x{FWSEC_IMAGE_END - 2:x}=0000:1111",
                     f"0x{len(self.rom) - 2:x}=0000:11111111",
                     f"0x{len(self.rom):x}=00:11"):
            with self.assertRaises(RomError, msg=spec):
                self.patch(spec)

    def test_refuses_a_wrong_belief_about_the_current_bytes(self):
        with self.assertRaises(RomError):
            self.patch(f"0x{FREE:x}=deadbeef:{NEUTRAL}")
        with self.assertRaises(RomError):
            self.patch(f"0x{FREE:x}={SS0_BYTES}:{SS0_BYTES}")
        with self.assertRaises(RomError):
            self.patch(self.edit, f"0x{FREE + 2:x}=8200:0000")
        with self.assertRaises(RomError):
            generate(self.rom, self.vault, self.sha, [])

    def test_refuses_a_vault_that_does_not_hold_this_rom(self):
        other = self.keep(bytes(fixture()), "other")
        with self.assertRaises(RomError):
            generate(self.rom, other, None, [self.edit])
        with self.assertRaises(RomError):
            generate(self.rom, self.root / "absent", self.sha, [self.edit])

    def test_refuses_when_the_scan_finds_no_editable_candidate(self):
        plain = bytes(fixture())
        vault = self.keep(plain, "plain")
        with self.assertRaises(RomError):
            generate(plain, vault, digest(plain), [f"0x{FREE:x}=00000000:11111111"])

    def test_validates_the_checksum_pad(self):
        with self.assertRaises(RomError):
            self.patch(pad=len(self.rom) + 16)
        with self.assertRaises(RomError):
            self.patch(pad=SIGNED_BLOCK)
        with self.assertRaises(RomError):
            self.patch(pad=FREE + 1)
        image, report = self.patch(pad=FREE + 8)
        self.assertEqual(report["checksums"][0]["pad_offset"], FREE + 8)
        self.assertEqual(sum(image) & 0xFF, 0)

    def test_rejects_malformed_edit_specifications(self):
        for spec in ("", "0x700", "0x700=aa", "0x700=:bb", "0x700=aa:bbcc",
                     "0x700=zz:bb", "nope=aa:bb", "-4=aa:bb"):
            with self.assertRaises(RomError, msg=spec):
                parse_edit(spec)

    def test_cli_writes_one_image_and_never_overwrites(self):
        output = self.root / "patched.bin"
        argv = [str(self.root / "vault-0.bin"), "--vault", str(self.vault),
                "--expected-sha256", self.sha, "--set", self.edit, "--output", str(output)]
        main(argv)
        self.assertEqual(len(output.read_bytes()), len(self.rom))
        with self.assertRaises(SystemExit) as refused:
            main(argv)
        self.assertEqual(refused.exception.code, 2)


if __name__ == "__main__":
    unittest.main()