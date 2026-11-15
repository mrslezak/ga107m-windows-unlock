"""Offline tests for the original-VBIOS custody tool; no GPU and no flashing."""

import json
import tempfile
import unittest
from pathlib import Path

from compare_vbios import RomError
from test_compare_vbios import fixture
from vbios_vault import DIGEST, MANIFEST, digest, pinned, store, verify


PORT_HEADER = Path(__file__).resolve().parent.parent / "unlock" / "port.h"


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.rom = bytes(fixture())
        self.sha = digest(self.rom)
        self.captures = []
        for index in range(2):
            path = self.root / f"capture-{index}.bin"
            path.write_bytes(self.rom)
            self.captures.append(path)
        self.vault = self.root / "vault"

    def keep(self, copies=3):
        return store(self.vault, self.captures, self.sha, copies)

    def test_store_then_verify_round_trip(self):
        manifest = self.keep()
        self.assertEqual(manifest["rom_sha256"], self.sha)
        self.assertTrue(manifest["matches_pinned_original"])
        self.assertEqual(len(manifest["copies"]), 3)
        report = verify(self.vault, self.sha)
        self.assertTrue(report["restore_ready"])
        self.assertEqual(len(report["verified_copies"]), 3)
        self.assertEqual(report["unexpected_files"], [])
        for entry in manifest["copies"]:
            self.assertEqual((self.vault / entry["name"]).read_bytes(), self.rom)

    def test_store_rejects_weak_or_mismatched_input(self):
        with self.assertRaises(RomError):
            store(self.vault, self.captures[:1], self.sha, 3)
        odd = self.root / "capture-odd.bin"
        odd.write_bytes(self.rom[:-1] + b"\xff")
        with self.assertRaises(RomError):
            store(self.vault, [self.captures[0], odd], self.sha, 3)
        with self.assertRaises(RomError):
            store(self.vault, self.captures, "0" * 64, 3)
        with self.assertRaises(RomError):
            store(self.vault, self.captures, self.sha, 0)
        self.assertFalse(self.vault.exists())

    def test_store_refuses_to_overwrite_an_existing_vault(self):
        self.keep()
        with self.assertRaises(RomError):
            self.keep()
        self.assertEqual(verify(self.vault, self.sha)["rom_sha256"], self.sha)

    def test_verify_detects_corruption_and_tampering(self):
        manifest = self.keep()
        damaged = self.vault / manifest["copies"][1]["name"]
        damaged.write_bytes(self.rom[:-1] + b"\xff")
        with self.assertRaises(RomError):
            verify(self.vault, self.sha)
        damaged.write_bytes(self.rom)
        self.assertTrue(verify(self.vault, self.sha)["restore_ready"])
        damaged.unlink()
        with self.assertRaises(RomError):
            verify(self.vault, self.sha)

    def test_verify_detects_an_edited_manifest(self):
        self.keep()
        record = json.loads((self.vault / MANIFEST).read_text(encoding="utf-8"))
        record["rom_sha256"] = "0" * 64
        (self.vault / MANIFEST).write_text(json.dumps(record, indent=2), encoding="utf-8")
        with self.assertRaises(RomError):
            verify(self.vault, self.sha)

    def test_verify_rejects_a_vault_that_is_not_the_pinned_original(self):
        self.keep()
        with self.assertRaises(RomError):
            verify(self.vault, "1" * 64)

    def test_verify_reports_unexpected_files(self):
        self.keep()
        (self.vault / "patched-candidate.bin").write_bytes(self.rom)
        self.assertEqual(verify(self.vault, self.sha)["unexpected_files"],
                         ["patched-candidate.bin"])

    def test_pin_is_read_from_the_project_header(self):
        self.assertTrue(PORT_HEADER.is_file(), "unlock/port.h is missing")
        value = pinned(PORT_HEADER)
        self.assertEqual(len(value), 64)
        self.assertEqual(value, value.lower())
        blank = self.root / "port.h"
        blank.write_text("#define GU_BOARD_ID 0x25a010deU\n", encoding="utf-8")
        with self.assertRaises(RomError):
            pinned(blank)

    def test_digest_file_guards_the_manifest(self):
        self.keep()
        self.assertEqual((self.vault / DIGEST).read_text(encoding="utf-8").strip(),
                         digest((self.vault / MANIFEST).read_text(encoding="utf-8").encode()))


if __name__ == "__main__":
    unittest.main()
