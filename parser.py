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

AUDIO_MIME = {
    "midi": "audio/midi",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "amr": "audio/amr",
    "ogg": "audio/ogg",
}


@dataclass
class Entry:
    path: str
    category: str
    size: int
    sha1: str
    mime: str
    previewable: bool
    audio_detected: bool = False
    audio_format: str | None = None
    audio_offset: int | None = None


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


def detect_audio_signature(data: bytes, path: str = "", scan_limit: int = 1024) -> dict[str, int | str] | None:
    path_lower = path.lower()

    ext_map = {
        ".mid": "midi",
        ".midi": "midi",
        ".wav": "wav",
        ".mp3": "mp3",
        ".amr": "amr",
        ".ogg": "ogg",
    }
    for ext, fmt in ext_map.items():
        if path_lower.endswith(ext):
            return {"format": fmt, "offset": 0, "mime": AUDIO_MIME[fmt]}

    chunk = data[:scan_limit]

    midi_pos = chunk.find(b"MThd")
    if midi_pos >= 0:
        return {"format": "midi", "offset": midi_pos, "mime": AUDIO_MIME["midi"]}

    riff_pos = chunk.find(b"RIFF")
    while riff_pos >= 0:
        if riff_pos + 12 <= len(chunk) and chunk[riff_pos + 8 : riff_pos + 12] == b"WAVE":
            return {"format": "wav", "offset": riff_pos, "mime": AUDIO_MIME["wav"]}
        riff_pos = chunk.find(b"RIFF", riff_pos + 1)

    id3_pos = chunk.find(b"ID3")
    if id3_pos >= 0:
        return {"format": "mp3", "offset": id3_pos, "mime": AUDIO_MIME["mp3"]}

    for i in range(max(0, len(chunk) - 1)):
        if chunk[i] == 0xFF and i + 1 < len(chunk) and chunk[i + 1] in (0xFB, 0xF3, 0xF2):
            return {"format": "mp3", "offset": i, "mime": AUDIO_MIME["mp3"]}

    amr_pos = chunk.find(b"#!AMR")
    if amr_pos >= 0:
        return {"format": "amr", "offset": amr_pos, "mime": AUDIO_MIME["amr"]}

    ogg_pos = chunk.find(b"OggS")
    if ogg_pos >= 0:
        return {"format": "ogg", "offset": ogg_pos, "mime": AUDIO_MIME["ogg"]}

    return None


def guess_category(path: str, data: bytes) -> str:
    audio_probe = detect_audio_signature(data, path)
    if audio_probe:
        return "🎵 Аудио"

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

            audio_probe = detect_audio_signature(data, clean_name)
            if audio_probe:
                mime = str(audio_probe["mime"])

            entries.append(
                Entry(
                    path=clean_name,
                    category=guess_category(clean_name, data),
                    size=info.file_size,
                    sha1=hashlib.sha1(data).hexdigest(),
                    mime=mime,
                    previewable=is_previewable(mime, clean_name),
                    audio_detected=bool(audio_probe),
                    audio_format=str(audio_probe["format"]) if audio_probe else None,
                    audio_offset=int(audio_probe["offset"]) if audio_probe else None,
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
                "audio_detected": entry.audio_detected,
                "audio_format": entry.audio_format,
                "audio_offset": entry.audio_offset,
            }
        )

    return {
        "session_id": session_id,
        "archive_name": display_name,
        "file_count": len(entries),
        "categories": categories,
    }
