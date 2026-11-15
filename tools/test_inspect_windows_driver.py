"""Synthetic tests; no driver file, Capstone, LIEF, registry or GPU required."""

import struct
import unittest

from inspect_windows_driver import containing, lea_candidates, occurrences


class DriverTriageTests(unittest.TestCase):
    def test_empty_pattern_refused(self):
        with self.assertRaises(ValueError):
            list(occurrences(b"abc", b""))

    def test_literal_overlaps(self):
        self.assertEqual(list(occurrences(b"aaaa", b"aa")), [0, 1, 2])

    def test_literal_not_regex(self):
        self.assertEqual(list(occurrences(b"a.b ab", b".")), [1])

    def test_lea_positive_displacement(self):
        code = b"\x48\x8d\x15" + struct.pack("<i", 0x100)
        self.assertEqual(list(lea_candidates(code, 0x2000, {0x2107})), [(0x2000, 0x2107)])

    def test_lea_negative_displacement(self):
        code = b"\x90\x4c\x8d\x05" + struct.pack("<i", -0x108)
        self.assertEqual(list(lea_candidates(code, 0x2000, {0x1f00})), [(0x2001, 0x1f00)])

    def test_unrelated_lea(self):
        code = b"\x48\x8d\x15" + struct.pack("<i", 0)
        self.assertEqual(list(lea_candidates(code, 0, {123})), [])

    def test_truncated_lea(self):
        self.assertEqual(list(lea_candidates(b"\x48\x8d\x15\0", 0, {0})), [])

    def test_fragment_end_excluded_and_gap(self):
        entries = [(10, 20, 1), (30, 40, 2)]
        starts = [10, 30]
        for rva in (0, 20, 29, 40):
            self.assertIsNone(containing(entries, starts, rva))
        self.assertEqual(containing(entries, starts, 10), entries[0])
        self.assertEqual(containing(entries, starts, 39), entries[1])


if __name__ == "__main__":
    unittest.main()