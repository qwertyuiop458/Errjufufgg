from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import zipfile
from dataclasses import dataclass
from uuid import uuid4


ARTIFACT_STORE: dict[str, dict[str, dict[str, bytes | str]]] = {}

CATEGORY_MAP = {
    ".png": "Графика / Текстуры",
    ".gif": "Графика / Текстуры",
    ".jpg": "Графика / Текстуры",
    ".jpeg": "Графика / Текстуры",
    ".bmp": "Графика / Текстуры",
    ".wbmp": "Графика / Текстуры",
    ".pal": "Палитры",
    ".act": "Палитры",
    ".mid": "Музыка / Звук",
    ".midi": "Музыка / Звук",
    ".wav": "Музыка / Звук",
    ".mp3": "Музыка / Звук",
    ".amr": "Музыка / Звук",
    ".mmf": "Музыка / Звук",
    ".txt": "Сценарий / Текст",
    ".json": "Сценарий / Текст",
    ".xml": "Сценарий / Текст",
    ".csv": "Сценарий / Текст",
    ".class": "Код / Классы",
    ".java": "Код / Исходники",
    ".sprite": "Спрайты / Анимации",
    ".atlas": "Спрайты / Анимации",
}


@dataclass
class Entry:
    path: str
    category: str
    size: int
    sha1: str
    mime: str
    previewable: bool


def sanitize_rel_path(path: str) -> str | None:
    clean = posixpath.normpath(path).lstrip("/")
    if clean.startswith("../") or clean == "..":
        return None
    return clean


def parse_jad(content: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def summarize_jad(jad: dict[str, str]) -> dict[str, str]:
    keys = [
        "MIDlet-Name",
        "MIDlet-Version",
        "MIDlet-Vendor",
        "MIDlet-Jar-URL",
        "MIDlet-Jar-Size",
        "MicroEdition-Profile",
        "MicroEdition-Configuration",
    ]
    return {k: jad[k] for k in keys if k in jad}


def guess_category(path: str) -> str:
    lower = path.lower()
    for ext, category in CATEGORY_MAP.items():
        if lower.endswith(ext):
            return category
    if "sprite" in lower or "anim" in lower:
        return "Спрайты / Анимации"
    if "concept" in lower or "art" in lower:
        return "Концепт-арт"
    return "Прочее"


def is_previewable(mime: str, path: str) -> bool:
    if mime.startswith("image/") or mime.startswith("audio/"):
        return True
    return path.lower().endswith((".txt", ".json", ".xml", ".csv", ".java"))


def analyze_archive(fileobj, display_name: str) -> dict:
    with zipfile.ZipFile(fileobj) as archive:
        entries: list[Entry] = []
        artifacts: dict[str, dict[str, bytes | str]] = {}

        for info in archive.infolist():
            if info.is_dir():
                continue
            data = archive.read(info.filename)
            mime = mimetypes.guess_type(info.filename)[0] or "application/octet-stream"
            clean_name = sanitize_rel_path(info.filename)
            if clean_name is None:
                continue

            entries.append(
                Entry(
                    path=clean_name,
                    category=guess_category(clean_name),
                    size=info.file_size,
                    sha1=hashlib.sha1(data).hexdigest(),
                    mime=mime,
                    previewable=is_previewable(mime, clean_name),
                )
            )
            artifacts[clean_name] = {"data": data, "mime": mime}

    session_id = uuid4().hex[:16]
    ARTIFACT_STORE[session_id] = artifacts

    categories: dict[str, list[dict]] = {}
    for entry in sorted(entries, key=lambda item: (item.category, item.path)):
        categories.setdefault(entry.category, []).append(
            {
                "path": entry.path,
                "size": entry.size,
                "sha1": entry.sha1,
                "mime": entry.mime,
                "previewable": entry.previewable,
            }
        )

    return {
        "session_id": session_id,
        "archive_name": display_name,
        "file_count": len(entries),
        "categories": categories,
    }
