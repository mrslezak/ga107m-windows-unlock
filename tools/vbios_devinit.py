"""Read-only search for VBIOS-resident references to the GA107M selector window."""

import argparse
import json
from pathlib import Path

from compare_vbios import MAX_ROM, RomError, analysis_view, inspect, number, read_rom


FEATURE_BASE = 0x00823800
FEATURE_SIZE = 0x40
NAMED = {0x00823804: "feature_plm", 0x0082381c: "ss0", 0x00823820: "ss1"}
SIGNED_PARTS = ("signatures", "descriptor", "image")
INIT_TOKEN = 0x49
CONTEXT = 8


def tokens(data):
    """Enumerate BIT tokens using the header validation from vendor/vbios_610.c."""
    for bit in range(len(data) - 11):
        if data[bit:bit + 6] != b"\xff\xb8BIT\x00":
            continue
        header, stride, count = data[bit + 8:bit + 11]
        if header < 12 or stride < 6 or bit + header > len(data):
            continue
        if sum(data[bit:bit + header]) & 0xff:
            continue
        number(data, bit + header, count * stride)
        entries = []
        for index in range(count):
            token = bit + header + index * stride
            identifier = data[token]
            entries.append({"offset": token, "id": identifier,
                            "name": chr(identifier) if 32 <= identifier < 127 else "",
                            "version": data[token + 1],
                            "length": number(data, token + 2, 2),
                            "pointer": number(data, token + 4, 4 if stride >= 8 else 2)})
        return {"offset": bit, "header_size": header, "entry_size": stride,
                "entry_count": count, "entries": entries}
    return None


def signed(summary):
    """Locate parsed FWSEC structures, not cryptographic coverage boundaries."""
    if not summary:
        return []
    return [{"name": "fwsec_" + part, "offset": summary[part]["offset"],
             "size": summary[part]["size"]} for part in SIGNED_PARTS]


def covering(regions, offset, size):
    for entry in regions:
        if entry["offset"] <= offset and offset + size <= entry["offset"] + entry["size"]:
            return entry["name"]
    return ""


def references(data, base, size, context, chain, protected):
    hits = []
    for offset in range(len(data) - 3):
        value = int.from_bytes(data[offset:offset + 4], "little")
        if not base <= value < base + size:
            continue
        region = covering(protected, offset, 4)
        start = max(0, offset - context)
        hits.append({"offset": offset, "address": f"0x{value:08x}",
                     "register": NAMED.get(value, ""), "aligned": not offset % 4,
                     "image": covering(chain, offset, 4), "signed_region": region,
                     "editable_candidate": not region,
                     "context_offset": start,
                     "context": data[start:offset + 4 + context].hex()})
    return hits


def verdict(hits, protected):
    outside = sum(not hit["signed_region"] for hit in hits)
    if not hits:
        return ("No ROM-resident reference to the selector window was found as a literal "
                "little-endian address. Encoded, computed, or compressed references may "
                "still exist; this does not rule out an initialization path.")
    if not outside:
        return (f"All {len(hits)} reference(s) lie in parsed FWSEC structures. "
                "Signature coverage and execution semantics have not been verified.")
    if not protected:
        return (f"{outside} reference(s) found, but no FWSEC region was parsed, so "
                "nothing was excluded as signed. Treat every hit as unclassified.")
    return (f"{outside} of {len(hits)} reference(s) lie outside parsed FWSEC structures. "
            "Their authentication and execution are unknown; they are not established "
            "editable locations. A byte match does not establish a register write.")


def analyze(data, base=FEATURE_BASE, size=FEATURE_SIZE, context=CONTEXT):
    data = bytes(data)
    if not 2 <= len(data) <= MAX_ROM:
        raise RomError("ROM size must be between 2 bytes and 1 MiB")
    if base < 0 or base % 4 or size < 4 or size > 0x10000 or base + size > 1 << 32:
        raise RomError("scan window must be 4-byte aligned, 4 bytes to 64 KiB, in 32-bit space")
    if not 0 <= context <= 64:
        raise RomError("context must be between 0 and 64 bytes")
    inventory = inspect(data)
    view, layout = analysis_view(data)
    origin = layout["file_offset"]
    regions = [{"name": f"image[{index}]", "offset": entry["offset"], "size": entry["size"]}
               for index, entry in enumerate(inventory["images"])]
    summary = inventory["fwsec"]
    protected = signed(summary)
    hits = references(data, base, size, context, regions, protected)
    for hit in hits:
        logical = hit["offset"] - origin
        hit["logical_offset"] = logical if 0 <= logical <= len(view) - 4 else None
        hit["region_classification"] = hit["signed_region"] or "authentication-unknown"
        hit["editability"] = "not-established"
        if layout["format"] != "raw":
            hit["editable_candidate"] = False
    table = tokens(view)
    if table:
        table["logical_offset"] = table["offset"]
        table["offset"] += origin
        for entry in table["entries"]:
            entry["logical_offset"] = entry["offset"]
            entry["offset"] += origin
    ids = [entry["id"] for entry in table["entries"]] if table else []
    return {"size": len(data), "sha256": inventory["sha256"],
            "input_format": inventory["input_format"], "analysis_view": layout,
            "offset_space": inventory["offset_space"],
            "legacy_fields": "signed_region and editable_candidate are legacy location hints, not signature or editability evidence",
            "window": {"base": f"0x{base:08x}", "size": size},
            "images": regions, "signed_regions": protected, "bit": table,
            "references": hits,
            "summary": {"references": len(hits),
                        "editable_candidates": sum(h["editable_candidate"] for h in hits),
                        "signed_references": sum(bool(h["signed_region"]) for h in hits),
                        "authentication_unknown_references": sum(not h["signed_region"] for h in hits),
                        "init_token_present": INIT_TOKEN in ids},
            "verdict": verdict(hits, protected),
            "warning": "Offline byte search only: it does not decode init bytecode, verify "
                       "signatures, prove which stage writes the selectors at power-on, or "
                       "establish that any edit is flashable or survives D3cold."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path, help="locally captured read-only PROM dump")
    parser.add_argument("--base", type=lambda value: int(value, 0), default=FEATURE_BASE,
                        help="first register address of the scan window")
    parser.add_argument("--size", type=lambda value: int(value, 0), default=FEATURE_SIZE,
                        help="scan window size in bytes")
    parser.add_argument("--context", type=int, default=CONTEXT,
                        help="bytes of surrounding context to record per hit")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(analyze(read_rom(args.rom), args.base, args.size, args.context),
                         indent=2))
    except (OSError, RomError, ValueError) as error:
        parser.exit(2, f"ROM selector scan failed: {error}\n")


if __name__ == "__main__":
    main()
