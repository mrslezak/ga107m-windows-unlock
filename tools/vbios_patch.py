"""Gated generator for a modified GA107M VBIOS image (never flashes)."""

import argparse
import json
from pathlib import Path

from compare_vbios import RomError, differences, inspect, read_rom
from vbios_devinit import FEATURE_BASE, FEATURE_SIZE, analyze
from vbios_vault import pinned, verify

SIGNED_KEYS = ("descriptor", "image", "signatures")
NOTE = ("Offline image generation only: this tool never accesses PCI or MMIO and never "
        "flashes. It proves an edit avoids the signed regions, keeps the option ROM "
        "geometry and restores the checksums the original maintained. It does not decode "
        "init bytecode, show the edited bytes are executed, verify any signature, or "
        "establish that the result boots or survives D3cold. Editing an address literal "
        "changes where a write lands, not the value written.")


def parse_edit(text):
    """Accept OFFSET=EXPECTED:REPLACEMENT so every edit states what it believes it overwrites."""
    location, _, values = text.partition("=")
    before, _, after = values.partition(":")
    if not location or not before or not after:
        raise RomError(f"edit {text!r} must read OFFSET=EXPECTEDHEX:REPLACEMENTHEX")
    try:
        offset = int(location, 0)
        expected = bytes.fromhex(before)
        replacement = bytes.fromhex(after)
    except ValueError:
        raise RomError(f"edit {text!r} needs a numeric offset and two hex byte strings")
    if offset < 0:
        raise RomError(f"edit {text!r} has a negative offset")
    if not expected or len(expected) != len(replacement):
        raise RomError(f"edit {text!r} must replace a non-empty run of equal length")
    return {"offset": offset, "size": len(expected),
            "expected": expected.hex(), "replacement": replacement.hex()}


def overlapping(regions, offset, size):
    """Any intersection, not containment: a straddling edit must not slip past a guard."""
    for entry in regions:
        if offset < entry["offset"] + entry["size"] and entry["offset"] < offset + size:
            return entry["name"]
    return ""


def placed(specs, data, protected, candidates):
    """Fail closed on edits that leave the ROM, overlap, change nothing, or touch signed bytes."""
    edits = sorted((parse_edit(text) for text in specs), key=lambda item: item["offset"])
    if not edits:
        raise RomError("supply at least one edit")
    end = 0
    for index, edit in enumerate(edits):
        offset, size = edit["offset"], edit["size"]
        edit["name"] = f"edit[{index}]"
        if offset + size > len(data):
            raise RomError(f"edit at 0x{offset:x} runs past the end of the ROM")
        if offset < end:
            raise RomError(f"edit at 0x{offset:x} overlaps an earlier edit")
        end = offset + size
        region = overlapping(protected, offset, size)
        if region:
            raise RomError(f"edit at 0x{offset:x} lies inside signed region {region}")
        actual = data[offset:offset + size].hex()
        if actual != edit["expected"]:
            raise RomError(f"edit at 0x{offset:x} expected {edit['expected']} "
                           f"but the ROM holds {actual}")
        if edit["expected"] == edit["replacement"]:
            raise RomError(f"edit at 0x{offset:x} changes nothing")
        edit["selector_reference"] = bool(overlapping(candidates, offset, size))
    return edits


def sums(data, chain):
    return [{"name": entry["name"], "offset": entry["offset"], "size": entry["size"],
             "checksum": sum(data[entry["offset"]:entry["offset"] + entry["size"]]) & 0xff}
            for entry in chain]


def repair(data, baseline, protected, edits, pad):
    """Restore only the checksums the original actually held; never invent one."""
    report = []
    for entry in baseline:
        offset, size = entry["offset"], entry["size"]
        current = sum(data[offset:offset + size]) & 0xff
        record = {"name": entry["name"], "checksum_before": entry["checksum"],
                  "checksum_after": current, "repaired": False, "pad_offset": None,
                  "note": ""}
        if entry["checksum"]:
            record["note"] = "original image did not sum to zero; checksum left untouched"
        elif current:
            slot = pad if pad is not None and offset <= pad < offset + size else offset + size - 1
            blocker = overlapping(protected, slot, 1) or overlapping(edits, slot, 1)
            if blocker:
                raise RomError(f"checksum pad 0x{slot:x} for {entry['name']} collides with {blocker}")
            data[slot] = (data[slot] - current) & 0xff
            record.update(repaired=True, pad_offset=slot,
                          checksum_after=sum(data[offset:offset + size]) & 0xff)
        report.append(record)
    return report


def preserved(original, patched):
    """The signed firmware and the image geometry must survive the edit byte for byte."""
    if [(entry["offset"], entry["size"]) for entry in original["images"]] != \
            [(entry["offset"], entry["size"]) for entry in patched["images"]]:
        raise RomError("the patch changed the option ROM image geometry")
    first, second = original["fwsec"], patched["fwsec"]
    if bool(first) != bool(second):
        raise RomError("the patch changed whether a FWSEC image is present")
    if first and any(first[key]["sha256"] != second[key]["sha256"] for key in SIGNED_KEYS):
        raise RomError("the patch altered signed FWSEC bytes")


def generate(data, vault, expected, specs, pad=None, base=FEATURE_BASE, size=FEATURE_SIZE):
    data = bytes(data)
    original = inspect(data)
    if original["input_format"] != "raw":
        raise RomError("NVFlash containers are supported for read-only analysis only; patching is not supported")
    custody = verify(vault, expected)
    if custody["rom_sha256"] != original["sha256"]:
        raise RomError("the vault does not hold this ROM; store this capture before patching")
    scan = analyze(data, base, size)
    if not scan["summary"]["editable_candidates"]:
        raise RomError("the scan found no editable selector reference; "
                       "no patched image follows from this evidence")
    candidates = [{"name": f"candidate@0x{hit['offset']:x}", "offset": hit["offset"], "size": 4}
                  for hit in scan["references"] if hit["editable_candidate"]]
    protected = scan["signed_regions"]
    if pad is not None and not overlapping(scan["images"], pad, 1):
        raise RomError(f"checksum pad 0x{pad:x} lies outside every option ROM image")
    edits = placed(specs, data, protected, candidates)
    patched = bytearray(data)
    for edit in edits:
        patched[edit["offset"]:edit["offset"] + edit["size"]] = bytes.fromhex(edit["replacement"])
    checksums = repair(patched, sums(data, scan["images"]), protected, edits, pad)
    result = inspect(bytes(patched))
    preserved(original, result)
    return bytes(patched), {
        "vault": custody, "original": {"size": original["size"], "sha256": original["sha256"]},
        "patched": {"size": result["size"], "sha256": result["sha256"]},
        "scan_summary": scan["summary"], "signed_regions": protected,
        "edits": edits, "checksums": checksums,
        "diff": differences(data, bytes(patched)),
        "signed_regions_unchanged": True, "warning": NOTE}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, help="the captured original PROM dump to patch")
    parser.add_argument("--vault", type=Path, required=True,
                        help="verified vault holding this exact original")
    parser.add_argument("--set", action="append", default=[], metavar="OFFSET=OLD:NEW",
                        help="replace OLD with NEW at OFFSET; both are hex byte runs of equal length")
    parser.add_argument("--output", type=Path, help="write the patched image here; never overwrites")
    parser.add_argument("--port-header", type=Path,
                        help="unlock/port.h to read the pinned PROM SHA-256 from")
    parser.add_argument("--expected-sha256", help="pinned original digest, overriding --port-header")
    parser.add_argument("--checksum-pad", type=lambda value: int(value, 0),
                        help="byte to absorb the checksum fixup instead of the image's last byte")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=FEATURE_BASE,
                        help="first register address of the selector scan window")
    parser.add_argument("--size", type=lambda value: int(value, 0), default=FEATURE_SIZE,
                        help="selector scan window size in bytes")
    args = parser.parse_args(argv)
    try:
        expected = args.expected_sha256
        if expected is not None:
            expected = expected.lower()
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise RomError("expected SHA-256 must be 64 hexadecimal digits")
        elif args.port_header is not None:
            expected = pinned(args.port_header)
        image, report = generate(read_rom(args.rom), args.vault, expected, args.set,
                                 args.checksum_pad, args.base, args.size)
        if args.output is not None:
            if args.output.exists():
                raise RomError(f"{args.output} exists; refusing to overwrite")
            args.output.write_bytes(image)
            report["written"] = str(args.output)
            report["flash"] = "Not performed here. Verify the vault again before any flash."
        else:
            report["written"] = None
            report["flash"] = "Dry run: no file was written. Pass --output to emit the image."
        print(json.dumps(report, indent=2))
    except (OSError, RomError, ValueError, KeyError) as error:
        parser.exit(2, f"VBIOS patch generation failed: {error}\n")


if __name__ == "__main__":
    main()