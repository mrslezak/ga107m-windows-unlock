"""Custody tool for the original GA107M VBIOS: store, verify and locate restore copies."""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from compare_vbios import RomError, inspect, read_rom


PIN = re.compile(r'#define\s+GU_CARD_PROFILE_PROM_SHA256\s+"([0-9a-fA-F]{64})"')
MANIFEST = "manifest.json"
DIGEST = "manifest.sha256"
COPY = "vbios-original.{:02d}.bin"
MIN_CAPTURES = 2
MAX_COPIES = 8
NOTE = ("Original VBIOS custody only. This tool never reads or writes the GPU and never "
        "flashes. A verified vault is a precondition for any patched image, not a recovery "
        "guarantee: restoring still needs a working flash path, and a card that will not "
        "respond cannot be rescued from software.")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pinned(path):
    """Read the exact-card PROM pin so the vault keeps one source of truth."""
    match = PIN.search(path.read_text(encoding="utf-8", errors="replace"))
    if not match:
        raise RomError(f"no GU_CARD_PROFILE_PROM_SHA256 pin in {path}")
    return match.group(1).lower()


def agreed(paths):
    """Require independent captures that agree byte for byte before trusting either."""
    if len(paths) < MIN_CAPTURES:
        raise RomError(f"supply at least {MIN_CAPTURES} independently captured dumps")
    data = None
    captures = []
    for path in paths:
        current = read_rom(path)
        if data is None:
            data = current
        elif current != data:
            raise RomError(f"{path.name} disagrees with {paths[0].name}; recapture before storing")
        captures.append({"name": path.name, "sha256": digest(current)})
    return data, captures


def store(vault, paths, expected, copies):
    if not 1 <= copies <= MAX_COPIES:
        raise RomError(f"copies must be between 1 and {MAX_COPIES}")
    if vault.exists() and any(vault.iterdir()):
        raise RomError(f"{vault} is not empty; refusing to overwrite a stored original")
    data, captures = agreed(paths)
    structure = inspect(data)
    rom = digest(data)
    if expected and rom != expected:
        raise RomError(f"capture {rom} does not match the pinned original {expected}")
    vault.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(copies):
        target = vault / COPY.format(index)
        target.write_bytes(data)
        written.append({"name": target.name, "sha256": digest(target.read_bytes())})
    manifest = {"created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "rom_size": len(data), "rom_sha256": rom, "expected_sha256": expected,
                "matches_pinned_original": rom == expected if expected else None,
                "captures": captures, "copies": written,
                "images": len(structure["images"]),
                "fwsec_present": bool(structure["fwsec"]), "warning": NOTE}
    text = json.dumps(manifest, indent=2)
    (vault / MANIFEST).write_text(text, encoding="utf-8")
    (vault / DIGEST).write_text(digest(text.encode()) + "\n", encoding="utf-8")
    return manifest


def verify(vault, expected):
    try:
        text = (vault / MANIFEST).read_text(encoding="utf-8")
        recorded = (vault / DIGEST).read_text(encoding="utf-8").split()
    except OSError as error:
        raise RomError(f"vault is unreadable: {error}")
    if not recorded or digest(text.encode()) != recorded[0].lower():
        raise RomError("manifest digest mismatch; the vault metadata changed after storing")
    manifest = json.loads(text)
    rom = manifest["rom_sha256"]
    if expected and rom != expected:
        raise RomError(f"stored original {rom} does not match the pinned {expected}")
    if not manifest["copies"]:
        raise RomError("manifest records no copies")
    good = []
    for entry in manifest["copies"]:
        path = vault / entry["name"]
        if not path.is_file():
            raise RomError(f"missing vault copy {entry['name']}")
        actual = digest(read_rom(path))
        if actual != entry["sha256"] or actual != rom:
            raise RomError(f"vault copy {entry['name']} is corrupt ({actual})")
        inspect(read_rom(path))
        good.append(entry["name"])
    stray = sorted(item.name for item in vault.iterdir()
                   if item.name not in good + [MANIFEST, DIGEST])
    return {"vault": str(vault), "rom_sha256": rom, "rom_size": manifest["rom_size"],
            "verified_copies": good, "unexpected_files": stray,
            "matches_pinned_original": manifest.get("matches_pinned_original"),
            "restore_ready": True, "warning": NOTE}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path, required=True, help="directory holding the original")
    parser.add_argument("--port-header", type=Path, help="unlock/port.h to read the pinned PROM SHA-256 from")
    parser.add_argument("--expected-sha256", help="pinned original digest, overriding --port-header")
    commands = parser.add_subparsers(dest="command", required=True)
    keep = commands.add_parser("store", help="ingest agreeing captures into a new vault")
    keep.add_argument("captures", type=Path, nargs="+", help="independent PROM dumps")
    keep.add_argument("--copies", type=int, default=3, help="redundant copies to write")
    commands.add_parser("verify", help="recheck a stored vault")
    commands.add_parser("restore", help="verify, then name the copy to flash with an external tool")
    args = parser.parse_args(argv)
    try:
        expected = args.expected_sha256
        if expected is not None:
            expected = expected.lower()
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise RomError("expected SHA-256 must be 64 hexadecimal digits")
        elif args.port_header is not None:
            expected = pinned(args.port_header)
        if args.command == "store":
            report = store(args.vault, args.captures, expected, args.copies)
        else:
            report = verify(args.vault, expected)
            if args.command == "restore":
                report["restore_with"] = str(args.vault / report["verified_copies"][0])
                report["flash"] = "Not performed here. Flash the named file with your own tool."
        print(json.dumps(report, indent=2))
    except (OSError, RomError, ValueError, KeyError) as error:
        parser.exit(2, f"VBIOS vault operation failed: {error}\n")


if __name__ == "__main__":
    main()
