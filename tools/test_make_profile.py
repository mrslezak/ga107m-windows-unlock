"""Tests for REPORT.TXT -> profile conversion."""

import tempfile
from pathlib import Path
import unittest

from make_profile import parse_profile


REPORT_OK = """
GOP PCI parent: status=0x0 id=0x9a498086 subsystem=0x88e5103c bdf=0:0:2.0
preflight pins for profile transcription: chip=0xb77000a1 gfw_plm=0x8b8f gfw_boot=0x3ff gfw_cpuctl=0x10 wpr2_hi=0x0 feat_plm=0xffffff8f memory=0x1000 ss0=0x53520035 ss1=0x5
PROM sha256=84e2444773201c9fd74d6e8984bf01d771433879f86e1c94dc91b2702f7949d7
bar0 observed: base=0x52000000 size=0x1000000
config[00]=25a010de 00100000 030200a1 00100000
config[10]=52000000 00000000 00000000 00000000
config[20]=00000000 00000000 00000000 88e5103c
config[30]=00000000 000000c8 00000000 00000000
config[40]=00000000 00000000 00000000 00000000
config[50]=00000000 00000000 00000000 00000000
config[60]=00000000 00000000 00000000 00000000
config[70]=00000000 00000000 00000000 00000000
config[80]=00000000 00000000 00000000 00000000
config[90]=00000000 00000000 00000000 00000000
config[a0]=00000000 00000000 00000000 00000000
config[b0]=00000000 00000000 00000000 00000000
config[c0]=00000000 00050011 00000000 00000000
config[d0]=00000000 00000000 00000000 00000000
config[e0]=00000000 00000000 00000000 00000000
config[f0]=00000000 00000000 00000000 00000000
""".strip()


class MakeProfileTests(unittest.TestCase):
    def write_report(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "REPORT.TXT"
        path.write_text(text, encoding="utf-8")
        return path

    def test_parse_profile_success(self):
        profile = parse_profile(self.write_report(REPORT_OK))
        self.assertEqual(profile["schema_version"], 1)
        self.assertEqual(profile["board"]["id"], "0x25a010de")
        self.assertEqual(profile["board"]["subsystem"], "0x88e5103c")
        self.assertEqual(profile["igpu"]["id"], "0x9a498086")
        self.assertEqual(profile["cold_pins"]["chip"], "0xb77000a1")
        self.assertEqual(profile["recommended_stock_profile"], "entryLatchedSs1Full")

    def test_rejects_erased_flash(self):
        report = REPORT_OK.replace("chip=0xb77000a1", "chip=0xffffffff")
        with self.assertRaises(ValueError):
            parse_profile(self.write_report(report))


if __name__ == "__main__":
    unittest.main()
