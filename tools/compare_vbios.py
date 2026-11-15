"""Read-only inventory and comparison of GA107M PROM dumps (no GPU access)."""

import argparse
import hashlib
import json
from pathlib import Path


MAX_ROM = 1024 * 1024
SIGNATURES = {b"\x55\xaa", b"VN", b"\x77\xbb"}
DATA_SIGNATURES = {b"PCIR", b"NPDS", b"RGIS"}


class RomError(ValueError):
    pass


def number(data, offset, size):
    if offset < 0 or offset + size > len(data):
        raise RomError("ROM structure points outside supplied bytes")
    return int.from_bytes(data[offset:offset + size], "little")


def region(data, offset, size):
    number(data, offset, size)
    return {"offset": offset, "size": size,
            "sha256": hashlib.sha256(data[offset:offset + size]).hexdigest()}


def images(data):
    pos = 0
    result = []
    base = extension = 0
    for _ in range(8):
        if data[pos:pos + 2] not in SIGNATURES:
            raise RomError("missing option ROM image signature")
        pcir = pos + number(data, pos + 0x18, 2)
        if data[pcir:pcir + 4] not in DATA_SIGNATURES:
            raise RomError("missing PCIR/NPDS/RGIS header")
        length = number(data, pcir + 0x10, 2) * 512
        kind = number(data, pcir + 0x14, 1)
        last = bool(number(data, pcir + 0x15, 1) & 0x80)
        npde = (pcir + number(data, pcir + 0x0a, 2) + 15) & ~15
        if data[npde:npde + 4] == b"NPDE":
            revision = number(data, npde + 4, 2)
            ext_length = number(data, npde + 6, 2)
            if revision in (0x100, 0x101):
                if ext_length < 10:
                    raise RomError("short NPDE header")
                number(data, npde, ext_length)
                sub_length = number(data, npde + 8, 2) * 512
                if ext_length > 10:
                    last = bool(number(data, npde + 10, 1) & 0x80)
                elif sub_length < length:
                    last = False
                length = sub_length
        if not length:
            raise RomError("zero-length option ROM image")
        result.append({"type": kind, "pcir_offset": pcir, **region(data, pos, length)})
        if not base and kind == 0:
            base = length
        if not extension and kind == 0xe0:
            extension = pos
        if last:
            break
        pos += length
    else:
        raise RomError("ROM image chain exceeds eight images")
    if extension and base and extension < base:
        raise RomError("expansion precedes base image")
    return result, extension - base if extension and base else 0


def fwsec(data, adjust):
    """Follow the BIT 'p' token and type 0x85 as in vendor/vbios_610.c."""
    for bit in range(len(data) - 11):
        if data[bit:bit + 6] != b"\xff\xb8BIT\x00":
            continue
        header, stride, count = data[bit + 8:bit + 11]
        if header < 12 or stride < 6 or bit + header > len(data):
            continue
        if sum(data[bit:bit + header]) & 0xff:
            continue
        number(data, bit + header, count * stride)
        for index in range(count):
            token = bit + header + index * stride
            if data[token] != 0x70 or data[token + 1] != 2 or number(data, token + 2, 2) < 4:
                continue
            ptr = number(data, token + 4, 4 if stride >= 8 else 2)
            number(data, ptr, number(data, token + 2, 2))
            table = adjust + number(data, ptr, 4)
            number(data, table, 6)
            version, table_header, entry_size, entries = data[table:table + 4]
            if version != 1 or table_header < 6 or entry_size < 6:
                raise RomError("unsupported firmware table")
            number(data, table + table_header, entry_size * entries)
            for entry_index in range(entries):
                entry = table + table_header + entry_index * entry_size
                if data[entry] != 0x85:
                    continue
                off = adjust + number(data, entry + 2, 4)
                header_value = number(data, off, 4)
                desc_size = header_value >> 16
                if not header_value & 1 or (header_value >> 8) & 0xff != 3 or desc_size < 44:
                    raise RomError("unsupported FWSEC descriptor")
                descriptor = region(data, off, desc_size)
                stored = number(data, off + 4, 4)
                if not stored or stored > MAX_ROM - 255:
                    raise RomError("invalid FWSEC image size")
                image_size = (stored + 255) & ~255
                image = region(data, off + desc_size, image_size)
                imem = number(data, off + 20, 4)
                dmem = number(data, off + 32, 4)
                pkc = number(data, off + 8, 4)
                interface = number(data, off + 12, 4)
                versions = number(data, off + 40, 2)
                signatures = number(data, off + 39, 1)
                sig_bytes = desc_size - 44
                if (not imem or not dmem or imem + dmem > image_size or
                        pkc + 384 > dmem or interface + 4 > dmem or
                        not versions or versions.bit_count() != signatures or
                        signatures * 384 != sig_bytes):
                    raise RomError("invalid FWSEC image or signature geometry")
                return {"bit_offset": bit, "ucode_table_offset": table,
                        "descriptor": descriptor, "image": image,
                        "signatures": region(data, off + 44, sig_bytes),
                        "engine_mask": number(data, off + 36, 2),
                        "ucode_id": number(data, off + 38, 1),
                        "signature_versions": versions}
    return None


def inspect(data):
    data = bytes(data)
    if not 2 <= len(data) <= MAX_ROM:
        raise RomError("ROM size must be between 2 bytes and 1 MiB")
    view, layout = analysis_view(data)
    chain, adjust = images(view)
    firmware = fwsec(view, adjust)
    origin = layout["file_offset"]
    mapped_chain = []
    for entry in chain:
        mapped = dict(entry, logical_offset=entry["offset"])
        mapped["offset"] += origin
        mapped["pcir_offset"] += origin
        mapped_chain.append(mapped)
    if firmware is not None:
        firmware = dict(firmware)
        for key in ("bit_offset", "ucode_table_offset"):
            firmware[key] += origin
        for key in ("descriptor", "image", "signatures"):
            entry = firmware[key]
            firmware[key] = dict(entry, logical_offset=entry["offset"],
                                 offset=entry["offset"] + origin)
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "input_format": layout["format"], "analysis_view": layout,
            "offset_space": "original file; logical offsets are relative to analysis view",
            "signature_coverage": "unknown; descriptor geometry is not signature verification",
            "images": mapped_chain, "expansion_adjust": adjust,
            "fwsec": firmware}


def analysis_view(data):
    """Select a bounded view; NVGI interpretation requires an agreeing ROM chain."""
    if not 2 <= len(data) <= MAX_ROM:
        raise RomError("ROM size must be between 2 bytes and 1 MiB")
    if not data.startswith(b"NVGI"):
        return data, {"format": "raw", "file_offset": 0, **region(data, 0, len(data))}
    candidates = []
    record = data.find(b"RFRD")
    while record >= 0:
        try:
            version = number(data, record + 4, 2)
            header = number(data, record + 6, 2)
            if version != 3 or header != 40:
                raise RomError("unsupported RFRD header")
            number(data, record, header)
            offset = number(data, record + 8, 4)
            size = number(data, record + 12, 4)
            if offset < record + header or not size:
                raise RomError("invalid RFRD payload range")
            region(data, offset, size)
            view = data[offset:offset + size]
            chain, _ = images(view)
            if chain[0]["type"] != 0 or chain[-1]["offset"] + chain[-1]["size"] != size:
                raise RomError("RFRD range does not match complete base ROM chain")
            if any(not entry["offset"] <= entry["pcir_offset"] or
                   entry["pcir_offset"] + 0x16 > entry["offset"] + entry["size"]
                   for entry in chain):
                raise RomError("image data header outside image")
            candidates.append((offset, size, record))
        except RomError:
            pass
        record = data.find(b"RFRD", record + 4)
    if len(candidates) != 1:
        raise RomError("NVGI requires exactly one supported RFRD v3 record with a complete ROM chain")
    offset, size, record = candidates[0]
    return data[offset:offset + size], {
        "format": "nvgi-rfrd-v3", "file_offset": offset,
        "record_file_offset": record, **region(data, offset, size),
        "completeness": "ROM view parsed; other NVGI components are opaque",
        "provenance": "RFRD offset/length cross-checked against complete option-ROM chain",
    }


def differences(original, other, limit=16):
    spans = []
    start = None
    count = 0
    for offset in range(max(len(original), len(other)) + 1):
        changed = (offset < max(len(original), len(other)) and
                   (offset >= min(len(original), len(other)) or original[offset] != other[offset]))
        if changed and start is None:
            start = offset
        elif not changed and start is not None:
            count += 1
            if count <= limit:
                spans.append({"offset": start, "size": offset - start})
            start = None
    return {"changed_ranges": spans, "total_changed_ranges": count,
            "truncated": count > limit}


def read_rom(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_ROM + 1)
    if len(data) > MAX_ROM:
        raise RomError("ROM exceeds the 1 MiB limit")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path, help="original read-only PROM dump")
    parser.add_argument("candidate", type=Path, nargs="?", help="optional second ROM to compare")
    parser.add_argument("--expected-sha256", help="optional expected original PROM SHA-256")
    args = parser.parse_args(argv)
    try:
        baseline = read_rom(args.original)
        report = {"original": inspect(baseline), "warning":
                  "Inventory only: hashes and byte matches do not validate signatures, executable init scripts, flashability, or persistence."}
        if args.expected_sha256 is not None:
            expected = args.expected_sha256.lower()
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise RomError("expected SHA-256 must be 64 hexadecimal digits")
            report["original_hash_matches_expected"] = report["original"]["sha256"] == expected
        if args.candidate is not None:
            candidate = read_rom(args.candidate)
            report["candidate"] = inspect(candidate)
            report["comparison"] = differences(baseline, candidate)
        print(json.dumps(report, indent=2))
    except (OSError, RomError) as error:
        parser.exit(2, f"ROM inventory failed: {error}\n")


if __name__ == "__main__":
    main()