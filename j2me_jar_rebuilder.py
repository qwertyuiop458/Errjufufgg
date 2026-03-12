#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MethodInfo:
    name: str
    desc: str


@dataclass
class ClassModel:
    path: str
    data: bytes
    cp_count: int
    cp_entries: list[dict | None]
    cp_end_offset: int
    this_name: str
    super_name: str
    methods: list[MethodInfo]
    interfaces: list[str]


def read_u1(data: bytes, off: int) -> tuple[int, int]:
    return data[off], off + 1


def read_u2(data: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from(">H", data, off)[0], off + 2


def read_u4(data: bytes, off: int) -> tuple[int, int]:
    return struct.unpack_from(">I", data, off)[0], off + 4


def parse_class(data: bytes, path: str) -> ClassModel:
    if data[:4] != b"\xCA\xFE\xBA\xBE":
        raise ValueError(f"{path}: not a class file")

    off = 8
    cp_count, off = read_u2(data, off)
    cp_entries: list[dict | None] = [None] * cp_count

    i = 1
    while i < cp_count:
        tag, off = read_u1(data, off)
        if tag == 1:  # Utf8
            ln, off = read_u2(data, off)
            b = data[off : off + ln]
            off += ln
            cp_entries[i] = {"tag": 1, "value": b.decode("utf-8", errors="replace")}
        elif tag in (3, 4):  # Integer, Float
            cp_entries[i] = {"tag": tag, "raw": data[off : off + 4]}
            off += 4
        elif tag in (5, 6):  # Long, Double (2 slots)
            cp_entries[i] = {"tag": tag, "raw": data[off : off + 8]}
            off += 8
            i += 1
        elif tag in (7, 8, 16, 19, 20):
            idx, off = read_u2(data, off)
            cp_entries[i] = {"tag": tag, "index": idx}
        elif tag in (9, 10, 11, 12, 18):
            a, off = read_u2(data, off)
            b, off = read_u2(data, off)
            cp_entries[i] = {"tag": tag, "a": a, "b": b}
        elif tag == 15:
            kind, off = read_u1(data, off)
            idx, off = read_u2(data, off)
            cp_entries[i] = {"tag": 15, "kind": kind, "index": idx}
        else:
            raise ValueError(f"{path}: unsupported constant pool tag {tag}")
        i += 1

    cp_end_offset = off

    access_flags, off = read_u2(data, off)
    this_class_idx, off = read_u2(data, off)
    super_class_idx, off = read_u2(data, off)

    def class_name_by_index(idx: int) -> str:
        if idx == 0:
            return ""
        cls = cp_entries[idx]
        if not cls or cls.get("tag") != 7:
            return ""
        utf = cp_entries[cls["index"]]
        if not utf or utf.get("tag") != 1:
            return ""
        return str(utf["value"])

    this_name = class_name_by_index(this_class_idx)
    super_name = class_name_by_index(super_class_idx)

    interfaces_count, off = read_u2(data, off)
    interfaces: list[str] = []
    for _ in range(interfaces_count):
        idx, off = read_u2(data, off)
        interfaces.append(class_name_by_index(idx))

    fields_count, off = read_u2(data, off)
    for _ in range(fields_count):
        _, off = read_u2(data, off)  # access
        _, off = read_u2(data, off)  # name
        _, off = read_u2(data, off)  # desc
        attr_count, off = read_u2(data, off)
        for _ in range(attr_count):
            _, off = read_u2(data, off)
            ln, off = read_u4(data, off)
            off += ln

    methods_count, off = read_u2(data, off)
    methods: list[MethodInfo] = []
    for _ in range(methods_count):
        _, off = read_u2(data, off)  # access
        name_idx, off = read_u2(data, off)
        desc_idx, off = read_u2(data, off)
        attr_count, off = read_u2(data, off)

        name_entry = cp_entries[name_idx] if name_idx < len(cp_entries) else None
        desc_entry = cp_entries[desc_idx] if desc_idx < len(cp_entries) else None
        n = str(name_entry.get("value")) if name_entry and name_entry.get("tag") == 1 else ""
        d = str(desc_entry.get("value")) if desc_entry and desc_entry.get("tag") == 1 else ""
        methods.append(MethodInfo(name=n, desc=d))

        for _ in range(attr_count):
            _, off = read_u2(data, off)
            ln, off = read_u4(data, off)
            off += ln

    return ClassModel(
        path=path,
        data=data,
        cp_count=cp_count,
        cp_entries=cp_entries,
        cp_end_offset=cp_end_offset,
        this_name=this_name,
        super_name=super_name,
        methods=methods,
        interfaces=interfaces,
    )


def is_obf_name(internal_name: str) -> bool:
    simple = internal_name.split("/")[-1]
    simple = simple.split("$")[-1]
    return bool(re.fullmatch(r"[a-z]{1,2}[0-9]?", simple))


def role_name(model: ClassModel) -> str:
    super_name = model.super_name
    method_names = {m.name for m in model.methods}
    cp_text = " ".join(str(e.get("value", "")) for e in model.cp_entries if e and e.get("tag") == 1)

    if "MIDlet" in super_name:
        return "MainMIDlet"
    if "GameCanvas" in super_name:
        return "GameScreen"
    if "Canvas" in super_name:
        return "MainCanvas"
    if "paint" in method_names:
        return "Renderer"
    if "keyPressed" in method_names:
        return "InputHandler"
    if "run" in method_names:
        return "GameLoop"
    if "RecordStore" in cp_text:
        return "SaveManager"
    return "Utils"


def build_mapping(models: list[ClassModel]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used: set[str] = set()

    for model in models:
        old = model.this_name
        pkg = "/".join(old.split("/")[:-1])
        base = role_name(model)

        if not is_obf_name(old):
            continue

        candidate = f"{pkg}/{base}" if pkg else base
        n = 2
        while candidate in used or candidate in mapping.values():
            candidate = f"{pkg}/{base}{n}" if pkg else f"{base}{n}"
            n += 1

        mapping[old] = candidate
        used.add(candidate)

    return mapping


def rewrite_class_names(model: ClassModel, mapping: dict[str, str]) -> bytes:
    cp = model.cp_entries

    def remap_text(text: str) -> str:
        out = text
        for old, new in mapping.items():
            out = out.replace(old, new)
            out = out.replace(old.replace("/", "."), new.replace("/", "."))
        return out

    for entry in cp:
        if entry and entry.get("tag") == 1:
            entry["value"] = remap_text(str(entry["value"]))

    out = bytearray()
    out.extend(model.data[:8])
    out.extend(struct.pack(">H", model.cp_count))

    i = 1
    while i < model.cp_count:
        entry = cp[i]
        if entry is None:
            i += 1
            continue
        tag = entry["tag"]
        out.append(tag)

        if tag == 1:
            raw = str(entry["value"]).encode("utf-8")
            out.extend(struct.pack(">H", len(raw)))
            out.extend(raw)
        elif tag in (3, 4):
            out.extend(entry["raw"])
        elif tag in (5, 6):
            out.extend(entry["raw"])
            i += 1
        elif tag in (7, 8, 16, 19, 20):
            out.extend(struct.pack(">H", entry["index"]))
        elif tag in (9, 10, 11, 12, 18):
            out.extend(struct.pack(">H", entry["a"]))
            out.extend(struct.pack(">H", entry["b"]))
        elif tag == 15:
            out.append(entry["kind"])
            out.extend(struct.pack(">H", entry["index"]))
        else:
            raise ValueError(f"Unsupported tag during rebuild: {tag}")
        i += 1

    out.extend(model.data[model.cp_end_offset:])
    return bytes(out)


def rewrite_manifest(manifest: str, mapping: dict[str, str]) -> str:
    updated = manifest
    for old, new in mapping.items():
        updated = updated.replace(old.replace("/", "."), new.replace("/", "."))
    return updated


def run_rebuilder(input_jar: Path, output_jar: Path, resolution: str | None = None) -> None:
    models: list[ClassModel] = []
    resources: list[tuple[zipfile.ZipInfo, bytes]] = []
    manifest_text = ""

    with zipfile.ZipFile(input_jar, "r") as zin:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.upper() == "META-INF/MANIFEST.MF":
                manifest_text = data.decode("utf-8", errors="ignore")
                resources.append((info, data))
                continue

            if info.filename.endswith(".class"):
                models.append(parse_class(data, info.filename))
            else:
                resources.append((info, data))

    mapping = build_mapping(models)

    structure_lines = []
    for m in models:
        new_name = mapping.get(m.this_name, m.this_name)
        structure_lines.append(f"{m.this_name} -> {new_name} | super={m.super_name} | methods={len(m.methods)}")

    with zipfile.ZipFile(output_jar, "w") as zout:
        for model in models:
            rewritten = rewrite_class_names(model, mapping)
            new_internal = mapping.get(model.this_name, model.this_name)
            out_name = f"{new_internal}.class"

            zi = zipfile.ZipInfo(out_name)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(zi, rewritten)

        for info, data in resources:
            out_name = info.filename
            payload = data

            if out_name.upper() == "META-INF/MANIFEST.MF":
                mtxt = manifest_text or data.decode("utf-8", errors="ignore")
                payload = rewrite_manifest(mtxt, mapping).encode("utf-8")

            zi = zipfile.ZipInfo(out_name)
            zi.compress_type = zipfile.ZIP_DEFLATED if out_name.endswith(".class") else zipfile.ZIP_STORED
            zout.writestr(zi, payload)

    mapping_path = output_jar.with_name("mapping.txt")
    with mapping_path.open("w", encoding="utf-8") as f:
        for old, new in sorted(mapping.items()):
            f.write(f"{old} -> {new}\n")

    structure_path = output_jar.with_name("structure.txt")
    with structure_path.open("w", encoding="utf-8") as f:
        if resolution:
            f.write(f"Resolution hint: {resolution}\n")
        f.write("\n".join(structure_lines))

    stats = {
        "input": str(input_jar),
        "output": str(output_jar),
        "renamed_classes": len(mapping),
        "mapping_file": str(mapping_path),
        "structure_file": str(structure_path),
        "resolution": resolution,
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="J2ME JAR Rebuilder (bytecode-level renaming)")
    parser.add_argument("input", help="Path to obfuscated JAR")
    parser.add_argument("--output", required=True, help="Output clean JAR path")
    parser.add_argument("--resolution", default=None, help="Resolution hint, e.g. 240x320")
    args = parser.parse_args()

    run_rebuilder(Path(args.input), Path(args.output), args.resolution)


if __name__ == "__main__":
    main()
