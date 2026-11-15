"""Read-only AMD64 PE triage. Never loads a driver or accesses the registry/GPU.

Optional analysis dependencies: LIEF and Capstone. Output goes to stdout only.
RIP-relative LEA matches are candidates until decoded from a runtime-function
entry. PE exception entries can describe fragments, not complete functions.
"""

import argparse
import bisect
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys


MAX_FILE = 256 * 1024 * 1024
LEA = re.compile(rb"[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]....", re.DOTALL)
WORDS = ("vbios", "vbiosSource", "romOverride", "RMExecuteDevinitOnPmu", "RMDevinitBySecureBoot")


def occurrences(data, needle):
    """Include overlapping matches, without treating binary bytes as regex."""
    if not needle:
        raise ValueError("empty search pattern")
    start = 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            return
        yield offset
        start = offset + 1


def lea_candidates(data, start_rva, targets):
    for match in LEA.finditer(data):
        rva = start_rva + match.start()
        target = rva + 7 + struct.unpack_from("<i", match.group(), 3)[0]
        if target in targets:
            yield rva, target


def containing(entries, starts, rva):
    index = bisect.bisect_right(starts, rva) - 1
    if index >= 0 and entries[index][0] <= rva < entries[index][1]:
        return entries[index]
    return None


def inspect(path, requested_rvas=()):
    # Conda-forge Capstone expects CONDA_PREFIX even in an unactivated process.
    if (Path(sys.prefix) / "conda-meta").is_dir():
        os.environ.setdefault("CONDA_PREFIX", sys.prefix)
    import capstone
    import lief

    if not 0 < path.stat().st_size <= MAX_FILE:
        raise ValueError("input must be a nonempty file no larger than 256 MiB")
    data = path.read_bytes()
    if len(data) > MAX_FILE:
        raise ValueError("input grew beyond size limit")
    pe = lief.PE.parse(data)
    if pe is None or int(pe.header.machine) != 0x8664:
        raise ValueError("input must be an AMD64 PE image")
    base = pe.optional_header.imagebase
    sections = list(pe.sections)

    def file_rva(offset):
        for section in sections:
            relative = offset - section.pointerto_raw_data
            if 0 <= relative < section.sizeof_raw_data:
                return section.virtual_address + relative
        return None

    def raw_range(start, end):
        for section in sections:
            relative = start - section.virtual_address
            if 0 <= relative and relative + end - start <= section.sizeof_raw_data:
                offset = section.pointerto_raw_data + relative
                return data[offset:offset + end - start]
        raise ValueError("runtime fragment is not wholly file-backed")

    strings = []
    targets = {}
    for word in WORDS:
        for encoding in ("ascii", "utf-16le"):
            needle = (word + "\0").encode(encoding)
            for offset in occurrences(data, needle):
                rva = file_rva(offset)
                strings.append(dict(word=word, encoding=encoding, file_offset=offset, rva=rva))
                if rva is not None:
                    targets[rva] = word
    entries = []
    for section in sections:
        if section.name == ".pdata":
            content = raw_range(section.virtual_address, section.virtual_address + section.sizeof_raw_data)
            for offset in range(0, len(content) - 11, 12):
                start, end, unwind = struct.unpack_from("<III", content, offset)
                if start and start < end:
                    entries.append((start, end, unwind))
    entries.sort()
    starts = [entry[0] for entry in entries]
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    decoded = {}

    def decode(entry):
        if entry not in decoded:
            start, end, _ = entry
            if end - start > 1024 * 1024:
                raise ValueError("runtime fragment exceeds 1 MiB decode budget")
            ins = list(md.disasm(raw_range(start, end), base + start))
            decoded[entry] = ins
        return decoded[entry]

    references = []
    for section in sections:
        if not int(section.characteristics) & 0x20000000:
            continue
        content = raw_range(section.virtual_address, section.virtual_address + section.sizeof_raw_data)
        for rva, target in lea_candidates(content, section.virtual_address, targets):
            entry = containing(entries, starts, rva)
            aligned = False
            if entry:
                aligned = any(i.address == base + rva and i.mnemonic == "lea" and i.size == 7 for i in decode(entry))
            references.append(dict(rva=rva, target_rva=target, word=targets[target],
                                   runtime_fragment=entry, entry_aligned=aligned))
    fragments = []
    for rva in requested_rvas:
        entry = containing(entries, starts, rva)
        if entry is None:
            raise ValueError(f"no runtime-function fragment contains {rva:#x}")
        ins = decode(entry)
        fragments.append(dict(requested_rva=rva, runtime_fragment=entry,
                              complete_linear_decode=bool(ins) and ins[-1].address + ins[-1].size == base + entry[1],
                              instructions=[dict(rva=i.address - base, mnemonic=i.mnemonic, operands=i.op_str) for i in ins]))
    return dict(schema_version=1, file=path.name, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                image_base=base, tools=dict(lief=lief.__version__, capstone=capstone.__version__),
                evidence_boundary="Static candidates only; linear decode is not execution or control-flow proof. Runtime entries may be fragments.",
                strings=strings, lea_references=references, fragments=fragments,
                selector_literals={hex(v): list(occurrences(data, struct.pack("<I", v))) for v in (0x82381c, 0x823820, 0x823804)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--fragment", type=lambda value: int(value, 0), action="append", default=[])
    args = parser.parse_args()
    try:
        result = inspect(args.image, args.fragment)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"inspection refused: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()