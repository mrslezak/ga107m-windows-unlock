"""Read-only BIT I / legacy NVIDIA initialization bytecode analysis (JSON only).

Format references, not copied implementations:
https://download.nvidia.com/open-gpu-doc/BIOS-Information-Table/1/BIOS-Information-Table.html
https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/bios/init.c
https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/nouveau/nvkm/subdev/devinit/gm200.c

This is a static graph decoder, NOT an emulator, authenticator or ROM editor.
It never assumes that syntactically reachable operations execute on this GPU.
"""

import argparse
from collections import deque
import hashlib
import json
from pathlib import Path

from compare_vbios import RomError, analysis_view, images, number
from compare_vbios import read_rom, region
from vbios_devinit import FEATURE_BASE, FEATURE_SIZE, NAMED, tokens


POINTERS = ("script_table", "macro_index", "macro_table", "condition_table",
            "io_condition_table", "io_flag_condition_table", "function_table",
            "additional_script", "xlat_table", "pcie_settings_script")
WARNING = ("Static legacy-bytecode analysis only. Conditions, repetitions and calls are "
           "not executed. Display/performance scripts, opaque tables, firmware machine "
           "code and computed runtime behavior are not exhaustively decoded. No signature "
           "is verified; no operation is proven authorized or executed at cold boot or "
           "D3cold wake. No editable/flashable/persistent unlock is established.")

# Only fixed lengths verified in the Nouveau reference are accepted here.
FIXED = {
    0x33: ("REPEAT", 2), 0x36: ("END_REPEAT", 1),
    0x38: ("NOT", 1), 0x39: ("IO_FLAG_CONDITION", 2),
    0x47: ("ANDN_REG", 9), 0x48: ("OR_REG", 9),
    0x52: ("CR", 4), 0x53: ("ZM_CR", 3),
    0x56: ("CONDITION_TIME", 3), 0x57: ("LTIME", 3),
    0x5b: ("SUB_DIRECT", 3), 0x5c: ("JUMP", 3),
    0x5f: ("COPY_NV_REG", 22), 0x62: ("ZM_INDEX_IO", 5),
    0x69: ("IO", 5), 0x6b: ("SUB", 2),
    0x6d: ("RAM_CONDITION", 3), 0x6e: ("NV_REG", 13),
    0x6f: ("MACRO", 2), 0x71: ("DONE", 1),
    0x72: ("RESUME", 1), 0x73: ("STRAP_CONDITION", 9),
    0x74: ("TIME", 3), 0x75: ("CONDITION", 2),
    0x76: ("IO_CONDITION", 2), 0x77: ("ZM_REG16", 7),
    0x78: ("INDEX_IO", 6), 0x7a: ("ZM_REG", 9),
    0x8c: ("RESET_BEGUN", 1), 0x8d: ("RESET_END", 1),
    0x90: ("COPY_ZM_REG", 9), 0x96: ("XLAT", 17),
    0x97: ("ZM_MASK_ADD", 13),
}
VARIABLE = {
    # name, header size, count byte, bytes per item
    0x32: ("IO_RESTRICT_PROG", 11, 6, 4),
    0x4c: ("I2C_BYTE", 4, 3, 3), 0x4d: ("ZM_I2C_BYTE", 4, 3, 2),
    0x4e: ("ZM_I2C", 4, 3, 1), 0x54: ("ZM_CR_GROUP", 2, 1, 2),
    0x58: ("ZM_REG_SEQUENCE", 6, 5, 4),
    0x91: ("ZM_REG_GROUP", 6, 5, 4),
}


def location(offset, origin):
    return {"logical_offset": offset, "file_offset": origin + offset}


def init_metadata(view, origin, max_scripts=256):
    # BIT_NVINIT_PTRS: ten u16 pointers (00..12), tables/scripts u16
    # pointer+length pairs (14..1a), NVLink data u16 (1c), non-GC6
    # script pointer+length (1e..20). Later fields stay uninterpreted.
    table = tokens(view)
    entries = [e for e in table["entries"] if e["id"] == ord("I")] if table else []
    if not entries:
        return None, []
    if len(entries) != 1:
        raise RomError("ambiguous BIT I entries")
    entry = entries[0]
    start, size = entry["pointer"], entry["length"]
    if entry["version"] != 1 or size < 14 or not start:
        raise RomError("unsupported BIT I version, length or null payload")
    number(view, start, size)
    pointers = {}
    for index, name in enumerate(POINTERS):
        if index * 2 + 2 <= min(size, 20):
            pointer = number(view, start + index * 2, 2)
            if pointer:
                number(view, pointer, 1)
            pointers[name] = pointer
    roots = []
    script_table = pointers["script_table"]
    if script_table:
        for index in range(max_scripts + 1):
            pointer = number(view, script_table + index * 2, 2)
            if not pointer:
                break
            if index == max_scripts:
                raise RomError("script table exceeds 256 roots or lacks terminator")
            number(view, pointer, 1)
            roots.append({"source": "BIT_I.script_table", "index": index,
                          **location(pointer, origin)})
    for name in ("additional_script", "pcie_settings_script"):
        if pointers.get(name):
            roots.append({"source": "BIT_I." + name,
                          **location(pointers[name], origin)})
    blobs = {}
    for name, field in (("pmu_tables", 0x14), ("pmu_boot_scripts", 0x18),
                        ("boot_scripts_non_gc6", 0x1e)):
        if size >= field + 4:
            pointer = number(view, start + field, 2)
            length = number(view, start + field + 2, 2)
            if bool(pointer) != bool(length):
                raise RomError(f"inconsistent {name} pointer/length")
            blobs[name] = ({**region(view, pointer, length),
                            **location(pointer, origin), "decoding": "opaque"}
                           if pointer else None)
    if size >= 0x1e:
        pointer = number(view, start + 0x1c, 2)
        if pointer:
            number(view, pointer, 1)
        pointers["nvlink_configuration"] = pointer
    metadata = {**location(start, origin), "version": entry["version"], "size": size,
                "pointers": pointers, "root_count": len(roots), "blobs": blobs,
                "uninterpreted_tail": view[start + min(size, 0x22):start + size].hex()}
    return metadata, roots


def decode_instruction(view, offset, pointers, origin):
    if not 0 < offset <= 0xffff:
        raise RomError("legacy bytecode offset exceeds the nonzero 16-bit address space")
    op = number(view, offset, 1)
    if op in FIXED:
        name, length = FIXED[op]
    elif op in VARIABLE:
        name, header, count_offset, stride = VARIABLE[op]
        number(view, offset, header)
        length = header + number(view, offset + count_offset, 1) * stride
    else:
        raise RomError(f"unsupported opcode 0x{op:02x}; decoding stops, no resynchronization")
    if offset + length > 0x10000:
        raise RomError("legacy instruction crosses the 16-bit address boundary")
    number(view, offset, length)
    read = lambda field, width=4: number(view, offset + field, width)
    node = {**location(offset, origin), "opcode": f"0x{op:02x}", "name": name,
            "size": length, "bytes": view[offset:offset + length].hex(),
            "writes": [], "successors": [], "runtime_execution": "unknown",
            "semantic_coverage": "framing/control only unless MMIO records or conditions are present"}

    def write(address, kind, value=None, value_offset=None, **extra):
        # Low two bits are flags; display head/OR/link flags require runtime context.
        flags = address & 0xe0000000
        normalized = address & ~3 if not flags else None
        item = {"address_operand": f"0x{address:08x}",
                "resolved_address": f"0x{normalized:08x}" if normalized is not None else None,
                "address_resolution": "requires runtime head/OR/link" if flags else "literal, low flag bits cleared",
                "kind": kind, "value": value, "authentication": "unknown",
                "selector_window_match": (FEATURE_BASE <= normalized < FEATURE_BASE + FEATURE_SIZE
                                          if normalized is not None else None),
                "register": NAMED.get(normalized, ""),
                **extra}
        if value_offset is not None:
            item["value_location"] = location(value_offset, origin)
        node["writes"].append(item)

    if op in (0x7a, 0x77):
        write(read(1), "constant", read(5, 2 if op == 0x77 else 4), offset + 5)
        if read(1) & ~3 == 0x200:
            node["semantic_note"] = "Nouveau forces bit 0 for ZM_REG at 0x200; stored value is not a full hardware model"
    elif op == 0x6e:
        write(read(1), "masked", read(9), offset + 9, and_mask=read(5),
              expression="(old & and_mask) | value")
    elif op in (0x47, 0x48):
        write(read(1), "masked", read(5), offset + 5,
              expression="old & ~value" if op == 0x47 else "old | value")
    elif op in (0x58, 0x91):
        for index in range(read(5, 1)):
            address = read(1) + (index * 4 if op == 0x58 else 0)
            if address > 0xffffffff:
                raise RomError("register sequence address overflows")
            field = 6 + index * 4
            write(address, "constant", read(field), offset + field)
    elif op == 0x32:
        node["io_selection"] = {"port": read(1, 2), "index": read(3, 1),
                    "mask": read(4, 1), "shift": read(5, 1),
                    "selected_index": "requires hardware read"}
        for index in range(read(6, 1)):
            field = 11 + index * 4
            write(read(7), "io-selected constant", read(field), offset + field,
                  selection_index=index)
    elif op == 0x90:
        write(read(5), "register copy", source_address=f"0x{read(1):08x}")
    elif op == 0x5f:
        write(read(14), "shifted masked register copy", source_address=f"0x{read(1):08x}",
              shift_encoding=read(5, 1), source_mask=read(6), source_xor=read(10),
              destination_and_mask=read(18))
    elif op == 0x96:
        write(read(8), "runtime table translation", source_address=f"0x{read(1):08x}",
              xlat_index=read(7, 1), destination_and_mask=read(12))
    elif op == 0x97:
        write(read(1), "masked add", read(9), offset + 9, and_mask=read(5),
              expression="(old & and_mask) | ((old + value) & ~and_mask)")
    elif op == 0x6f:
        table = pointers.get("macro_table", 0)
        if not table:
            raise RomError("MACRO has no supported macro table")
        record = table + read(1, 1) * 8
        address = number(view, record, 4)
        value = number(view, record + 4, 4)
        write(address, "macro constant", value, record + 4)
        node["semantic_note"] = "MACRO interpretation follows Nouveau; alternate index-table semantics not modeled"

    if op in (0x75, 0x56):
        table = pointers.get("condition_table", 0)
        if not table:
            raise RomError("condition has no supported condition table")
        record = table + read(1, 1) * 12
        node["condition"] = {**location(record, origin),
                             "address": f"0x{number(view, record, 4):08x}",
                             "and_mask": number(view, record + 4, 4),
                             "equals": number(view, record + 8, 4), "result": "unknown"}
    if op in (0x5b, 0x5c, 0x6b):
        if op == 0x6b:
            table = pointers.get("script_table", 0)
            if not table:
                raise RomError("SUB has no script table")
            target = number(view, table + read(1, 1) * 2, 2)
        else:
            target = read(1, 2)
        if target:
            node["successors"].append({"kind": "jump_or_call", "logical_offset": target})
    if op != 0x71:
        # Include fallthrough even for conditional jumps; no predicate emulation.
        node["successors"].append({"kind": "fallthrough", "logical_offset": offset + length})
    return node


def decode(data, scripts=(), max_instructions=4096):
    if not 1 <= max_instructions <= 16384:
        raise RomError("instruction budget must be between 1 and 16384")
    data = bytes(data)
    view, layout = analysis_view(data)
    origin = layout["file_offset"]
    # analysis_view selects raw inputs unchanged; validate their ROM chain as well.
    images(view)
    metadata, roots = init_metadata(view, origin)
    pointers = metadata["pointers"] if metadata else {}
    if len(scripts) > 256:
        raise RomError("at most 256 explicit script roots are allowed")
    for offset in scripts:
        if not 0 < offset <= 0xffff:
            raise RomError("explicit roots must be nonzero 16-bit logical ROM offsets")
        number(view, offset, 1)
        roots.append({"source": "user-specified; execution unproven", **location(offset, origin)})
    pending = deque(root["logical_offset"] for root in roots)
    attempted = set()
    owner = {}
    nodes, issues = [], []
    write_count = 0
    while pending:
        offset = pending.popleft()
        if offset in attempted:
            continue
        if len(attempted) >= max_instructions:
            issues.append({"reason": "instruction budget exhausted", "pending": len(pending) + 1})
            break
        attempted.add(offset)
        try:
            if offset in owner:
                raise RomError("control flow enters a decoded operand")
            node = decode_instruction(view, offset, pointers, origin)
            occupied = range(offset, offset + node["size"])
            if any(byte in owner for byte in occupied):
                raise RomError("instruction overlaps an existing decode")
            if write_count + len(node["writes"]) > 8192:
                raise RomError("write-record budget exhausted")
            write_count += len(node["writes"])
            for byte in occupied:
                owner[byte] = offset
            nodes.append(node)
            pending.extend(edge["logical_offset"] for edge in node["successors"])
        except RomError as error:
            issues.append({**location(offset, origin), "reason": str(error)})
    writes = [{**location(node["logical_offset"], origin), "instruction": node["name"],
               **write} for node in nodes for write in node["writes"]]
    matches = [write for write in writes if write["selector_window_match"]]
    unresolved = [write for write in writes if write["resolved_address"] is None]
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "analysis_view": layout, "bit_init": metadata, "roots": roots,
            "instructions": sorted(nodes, key=lambda node: node["logical_offset"]),
            "issues": issues, "selector_operations": matches,
            "unresolved_address_operations": unresolved,
            "summary": {"roots": len(roots), "instructions": len(nodes),
                        "write_records": write_count, "selector_operations": len(matches),
                        "unresolved_addresses": len(unresolved),
                        "issues": len(issues), "patch_eligible": False},
            "coverage": {"legacy_graph": ("partial" if issues else
                                          "decoded discovered roots only" if roots else "no roots discovered"),
                         "complete_gpu_initialization": False,
                         "write_records": "decoded MMIO possibilities only; port/I2C writes not enumerated",
                         "table_extents": "ROM bounds checked; uncounted table extents not independently known",
                         "authentication": "not verified", "persistence": "not established"},
            "verdict": ("No validated persistent ROM patch can be generated. "
                        "Decoded operations are static possibilities, not proof of execution or editability."),
            "warning": WARNING}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rom", type=Path)
    parser.add_argument("--script", type=lambda text: int(text, 0), action="append", default=[],
                        help="additional logical ROM script offset; does not prove execution")
    parser.add_argument("--max-instructions", type=int, default=4096)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(decode(read_rom(args.rom), args.script, args.max_instructions), indent=2))
    except (OSError, RomError) as error:
        parser.exit(2, f"ROM init decoding failed: {error}\n")


if __name__ == "__main__":
    main()