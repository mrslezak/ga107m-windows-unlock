#!/usr/bin/env python3
"""Build a machine profile JSON from a GA107M REPORT.TXT.

Usage:
  python tools/make_profile.py --report <RUNxx/REPORT.TXT> --output <profile.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

HEX_RE = r"(?:0x)?[0-9a-f]+"


def to_hex(token: str) -> str:
    return f"0x{int(token, 16):x}"


def find_last(text: str, pattern: str, label: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
    if not matches:
        raise ValueError(f"Missing {label} in report")
    return matches[-1]


def parse_config_dwords(text: str) -> list[int]:
    dwords: list[int | None] = [None] * 64
    for match in re.finditer(
        r"config\[([0-9a-f]{2})\]=([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})",
        text,
        flags=re.IGNORECASE,
    ):
        base = int(match.group(1), 16)
        for idx in range(4):
            dwords[(base // 4) + idx] = int(match.group(2 + idx), 16)
    if any(value is None for value in dwords):
        raise ValueError("Config dump is incomplete; expected 64 dwords")
    return [int(v) for v in dwords]


def parse_profile(report_path: Path) -> dict:
    raw = report_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")

    pins = find_last(
        text,
        rf"preflight pins for profile transcription: chip=({HEX_RE}) gfw_plm=({HEX_RE}) gfw_boot=({HEX_RE}) gfw_cpuctl=({HEX_RE}) wpr2_hi=({HEX_RE}) feat_plm=({HEX_RE}) memory=({HEX_RE}) ss0=({HEX_RE}) ss1=({HEX_RE})",
        "preflight pins",
    )
    prom = find_last(text, r"PROM sha256=([0-9a-f]{64})", "PROM sha256")
    bar = find_last(
        text,
        rf"bar0 (?:latched|observed): base=({HEX_RE}) size=({HEX_RE})",
        "BAR0",
    )

    dwords = parse_config_dwords(text)
    board_id = dwords[0]
    class_dword = dwords[2]
    subsystem = dwords[0x2C // 4]

    igpu_matches = [
        m
        for m in re.finditer(
            r"GOP PCI parent: status=0x[0-9a-f]+ id=(0x[0-9a-f]+) subsystem=(0x[0-9a-f]+) bdf=(\d+):(\d+):(\d+)\.(\d+)",
            text,
            flags=re.IGNORECASE,
        )
    ]
    igpu_id = None
    igpu_subsystem = None
    for match in igpu_matches:
        pid = int(match.group(1), 16)
        seg = int(match.group(3))
        bus = int(match.group(4))
        dev = int(match.group(5))
        fn = int(match.group(6))
        if (pid & 0xFFFF) == 0x8086 and seg == 0 and bus == 0 and dev == 2 and fn == 0:
            igpu_id = to_hex(match.group(1))
            igpu_subsystem = to_hex(match.group(2))
            break
    if igpu_id is None or igpu_subsystem is None:
        raise ValueError("Could not infer Intel GOP parent (00:00:02.0) from report")

    chip = to_hex(pins.group(1))
    if int(chip, 16) == 0xFFFFFFFF:
        raise ValueError("Cold pins still erased-flash; run a decode-enabled probe and retry")

    ss1 = to_hex(pins.group(9))
    recommended = "entryLatchedSs1Preserve"
    if int(ss1, 16) <= 0xF:
        recommended = "entryLatchedSs1Full"

    return {
        "schema_version": 1,
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "board": {
            "id": f"0x{board_id:x}",
            "subsystem": f"0x{subsystem:x}",
            "class_dword": f"0x{class_dword:x}",
            "target_pci_id": f"0x{board_id:x}",
        },
        "igpu": {
            "id": igpu_id,
            "subsystem": igpu_subsystem,
        },
        "bar0": {
            "base": to_hex(bar.group(1)),
            "size": to_hex(bar.group(2)),
        },
        "prom_sha256": prom.group(1).lower(),
        "cold_pins": {
            "chip": chip,
            "gfw_plm": to_hex(pins.group(2)),
            "gfw_boot": to_hex(pins.group(3)),
            "gfw_cpuctl": to_hex(pins.group(4)),
            "wpr2_hi": to_hex(pins.group(5)),
            "feat_plm": to_hex(pins.group(6)),
            "memory": to_hex(pins.group(7)),
            "ss0": to_hex(pins.group(8)),
            "ss1": ss1,
        },
        "recommended_stock_profile": recommended,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    profile = parse_profile(args.report.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote profile: {args.output}")
    print(f"Board ID: {profile['board']['id']}  Subsystem: {profile['board']['subsystem']}")
    print(f"Recommended stock profile: {profile['recommended_stock_profile']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
