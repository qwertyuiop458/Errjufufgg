from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from parser import (
    ARTIFACT_STORE,
    analyze_archive,
    detect_audio_signature,
    parse_jad,
    sanitize_rel_path,
    summarize_jad,
)

app = Flask(__name__)

CFR_URL = "https://github.com/leibnitz27/cfr/releases/download/0.152/cfr-0.152.jar"
CFR_PATH = Path(__file__).resolve().parent / "cfr.jar"
JAVA_SETUP_STATE = {"checked": False, "ok": False, "error": None}
FFMPEG_STATE = {"checked": False, "ok": False, "error": None}

AUDIO_FORMAT_META = {
    "midi": {"ext": "mid", "mime": "audio/midi"},
    "wav": {"ext": "wav", "mime": "audio/wav"},
    "mp3": {"ext": "mp3", "mime": "audio/mpeg"},
    "amr": {"ext": "amr", "mime": "audio/amr"},
    "ogg": {"ext": "ogg", "mime": "audio/ogg"},
}


def ensure_java_and_cfr(force: bool = False) -> tuple[bool, str | None]:
    if JAVA_SETUP_STATE["checked"] and not force:
        return bool(JAVA_SETUP_STATE["ok"]), JAVA_SETUP_STATE["error"]

    java_ok = shutil.which("java") is not None
    if java_ok:
        java_check = subprocess.run(["java", "-version"], capture_output=True, text=True, timeout=15)
        java_ok = java_check.returncode == 0

    if not java_ok:
        try:
            subprocess.run(["apt-get", "update"], check=True, capture_output=True, text=True, timeout=120)
            subprocess.run(
                ["apt-get", "install", "-y", "default-jdk"],
                check=True,
                capture_output=True,
                text=True,
                timeout=240,
            )
            java_ok = shutil.which("java") is not None
        except Exception as exc:  # noqa: BLE001
            JAVA_SETUP_STATE.update(
                {
                    "checked": True,
                    "ok": False,
                    "error": f"Java не установлена и автоустановка не удалась: {exc}",
                }
            )
            return False, str(JAVA_SETUP_STATE["error"])

    if not CFR_PATH.exists():
        try:
            with urllib.request.urlopen(CFR_URL, timeout=30) as response:
                CFR_PATH.write_bytes(response.read())
        except Exception as exc:  # noqa: BLE001
            JAVA_SETUP_STATE.update(
                {"checked": True, "ok": False, "error": f"Не удалось скачать CFR: {exc}"}
            )
            return False, str(JAVA_SETUP_STATE["error"])

    JAVA_SETUP_STATE.update({"checked": True, "ok": True, "error": None})
    return True, None


def ensure_ffmpeg(force: bool = False) -> tuple[bool, str | None]:
    if FFMPEG_STATE["checked"] and not force:
        return bool(FFMPEG_STATE["ok"]), FFMPEG_STATE["error"]

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    if not ffmpeg_ok:
        try:
            subprocess.run(["apt-get", "update"], check=True, capture_output=True, text=True, timeout=120)
            subprocess.run(
                ["apt-get", "install", "-y", "ffmpeg"],
                check=True,
                capture_output=True,
                text=True,
                timeout=240,
            )
            ffmpeg_ok = shutil.which("ffmpeg") is not None
        except Exception as exc:  # noqa: BLE001
            FFMPEG_STATE.update(
                {
                    "checked": True,
                    "ok": False,
                    "error": f"FFmpeg не установлен и автоустановка не удалась: {exc}",
                }
            )
            return False, str(FFMPEG_STATE["error"])

    FFMPEG_STATE.update({"checked": True, "ok": ffmpeg_ok, "error": None if ffmpeg_ok else "FFmpeg не найден"})
    return bool(FFMPEG_STATE["ok"]), FFMPEG_STATE["error"]


def build_hex_preview(data: bytes, limit: int = 256) -> str:
    chunk = data[:limit]
    lines: list[str] = []
    for i in range(0, len(chunk), 16):
        part = chunk[i : i + 16]
        hex_values = " ".join(f"{b:02x}" for b in part)
        ascii_values = "".join(chr(b) if 32 <= b <= 126 else "." for b in part)
        lines.append(f"{i:08x}  {hex_values:<47}  {ascii_values}")
    return "\n".join(lines)


def estimate_duration_seconds(data: bytes, fmt: str, offset: int = 0) -> float | None:
    if fmt != "wav":
        return None
    if offset + 44 > len(data):
        return None
    chunk = data[offset:]
    if chunk[:4] != b"RIFF" or chunk[8:12] != b"WAVE":
        return None

    byte_rate = int.from_bytes(chunk[28:32], "little", signed=False)
    data_size = int.from_bytes(chunk[40:44], "little", signed=False)
    if byte_rate <= 0:
        return None
    return round(data_size / byte_rate, 2)


def scan_audio_signatures(data: bytes, scan_limit: int = 1024) -> list[dict[str, int | str]]:
    chunk = data[:scan_limit]
    found: list[dict[str, int | str]] = []

    def push(fmt: str, offset: int, sig: str):
        found.append(
            {
                "format": fmt,
                "offset": offset,
                "offset_hex": f"0x{offset:08x}",
                "signature": sig,
                "mime": AUDIO_FORMAT_META[fmt]["mime"],
                "extension": AUDIO_FORMAT_META[fmt]["ext"],
            }
        )

    start = 0
    while True:
        pos = chunk.find(b"MThd", start)
        if pos < 0:
            break
        push("midi", pos, "MThd")
        start = pos + 1

    start = 0
    while True:
        pos = chunk.find(b"RIFF", start)
        if pos < 0:
            break
        if pos + 12 <= len(chunk) and chunk[pos + 8 : pos + 12] == b"WAVE":
            push("wav", pos, "RIFF...WAVE")
        start = pos + 1

    start = 0
    while True:
        pos = chunk.find(b"ID3", start)
        if pos < 0:
            break
        push("mp3", pos, "ID3")
        start = pos + 1

    for i in range(max(0, len(chunk) - 1)):
        if chunk[i] == 0xFF and i + 1 < len(chunk) and chunk[i + 1] in (0xFB, 0xF3):
            push("mp3", i, "FF FB/F3")

    start = 0
    while True:
        pos = chunk.find(b"#!AMR", start)
        if pos < 0:
            break
        push("amr", pos, "#!AMR")
        start = pos + 1

    start = 0
    while True:
        pos = chunk.find(b"OggS", start)
        if pos < 0:
            break
        push("ogg", pos, "OggS")
        start = pos + 1

    uniq: dict[tuple[str, int], dict[str, int | str]] = {}
    for row in found:
        uniq[(str(row["format"]), int(row["offset"]))] = row

    return sorted(uniq.values(), key=lambda x: int(x["offset"]))


def convert_audio_bytes_to_wav(data: bytes, input_ext: str) -> tuple[bytes | None, str | None]:
    ok, err = ensure_ffmpeg()
    if not ok:
        return None, err

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            src = tmp_dir / f"source.{input_ext}"
            dst = tmp_dir / "out.wav"
            src.write_bytes(data)

            proc = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), str(dst)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0 or not dst.exists():
                return None, (proc.stderr or proc.stdout or "ffmpeg conversion failed")
            return dst.read_bytes(), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/analyze")
def analyze():
    uploaded = request.files.get("file")
    companion_jar = request.files.get("jar_file")

    if not uploaded or uploaded.filename == "":
        return jsonify({"error": "Файл не был загружен."}), 400

    filename = uploaded.filename
    suffix = Path(filename).suffix.lower()

    if suffix not in {".jar", ".jad"}:
        return jsonify({"error": "Поддерживаются только .jar и .jad файлы."}), 400

    if suffix == ".jad":
        jad_text = uploaded.stream.read().decode("utf-8", errors="ignore")
        jad_data = parse_jad(jad_text)
        jar_name = jad_data.get("MIDlet-Jar-URL", "")

        jar_bytes: bytes | None = None
        if companion_jar and companion_jar.filename:
            jar_bytes = companion_jar.stream.read()
        elif jar_name and jar_name.endswith(".jar"):
            return jsonify(
                {
                    "error": "Загружен JAD. Добавьте соответствующий JAR в поле \"Companion JAR\".",
                    "jad": summarize_jad(jad_data),
                }
            ), 400

        if jar_bytes is None:
            return jsonify(
                {
                    "error": "JAD разобран, но JAR не найден. Загрузите JAR для просмотра ресурсов.",
                    "jad": summarize_jad(jad_data),
                }
            ), 400

        try:
            result = analyze_archive(io.BytesIO(jar_bytes), display_name=jar_name or "from_jad.jar")
        except zipfile.BadZipFile:
            return jsonify({"error": "Companion JAR повреждён или не является ZIP/JAR."}), 400

        result["jad"] = summarize_jad(jad_data)
        return jsonify(result)

    try:
        jar_bytes = uploaded.stream.read()
        return jsonify(analyze_archive(io.BytesIO(jar_bytes), display_name=filename))
    except zipfile.BadZipFile:
        return jsonify({"error": "Файл JAR повреждён или не является ZIP архивом."}), 400


@app.get("/audio_scan/<session_id>/<path:raw_path>")
def audio_scan(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    data = entry["data"]
    if not isinstance(data, bytes):
        return jsonify({"error": "Некорректные данные артефакта"}), 500

    signatures = scan_audio_signatures(data)
    return jsonify({"found": bool(signatures), "signatures": signatures, "scan_bytes": min(1024, len(data))})


@app.get("/audio_probe/<session_id>/<path:raw_path>")
def audio_probe(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    data = entry["data"]
    if not isinstance(data, bytes):
        return jsonify({"error": "Некорректные данные артефакта"}), 500

    probe = detect_audio_signature(data, safe_path)
    if not probe:
        return jsonify({"found": False, "error": "Музыкальная сигнатура не найдена"}), 200

    fmt = str(probe["format"])
    offset = int(probe["offset"])
    duration = estimate_duration_seconds(data, fmt, offset)

    return jsonify(
        {
            "found": True,
            "format": fmt,
            "offset": offset,
            "offset_hex": f"0x{offset:08x}",
            "mime": str(probe["mime"]),
            "duration_seconds": duration,
        }
    )


@app.get("/audio_extract/<session_id>/<path:raw_path>")
def audio_extract(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    data = entry["data"]
    if not isinstance(data, bytes):
        return jsonify({"error": "Некорректные данные артефакта"}), 500

    signatures = scan_audio_signatures(data)
    if not signatures:
        return jsonify({"error": "Аудиосигнатуры не найдены"}), 400

    offset = int(request.args.get("offset", signatures[0]["offset"]))
    signature_offsets = [int(s["offset"]) for s in signatures]
    if offset not in signature_offsets:
        return jsonify({"error": "Запрошенный offset не соответствует найденным сигнатурам"}), 400

    idx = signature_offsets.index(offset)
    end_offset = signature_offsets[idx + 1] if idx + 1 < len(signature_offsets) else len(data)
    fmt = str(signatures[idx]["format"])
    extracted = data[offset:end_offset]

    mode = request.args.get("mode", "stream")
    convert = request.args.get("convert", "0") in {"1", "true", "yes"}

    out_data = extracted
    out_fmt = fmt
    out_mime = AUDIO_FORMAT_META[fmt]["mime"]
    out_ext = AUDIO_FORMAT_META[fmt]["ext"]

    if convert or fmt == "amr":
        converted, err = convert_audio_bytes_to_wav(extracted, out_ext)
        if converted is None:
            if mode == "json":
                return jsonify({"error": f"Конвертация не удалась: {err}"}), 200
        else:
            out_data = converted
            out_fmt = "wav"
            out_mime = "audio/wav"
            out_ext = "wav"

    if mode == "json":
        return jsonify(
            {
                "ok": True,
                "format": out_fmt,
                "mime": out_mime,
                "offset": offset,
                "end_offset": end_offset,
                "size": len(out_data),
                "stream_url": f"/audio_extract/{session_id}/{safe_path}?offset={offset}&convert={int(convert)}",
                "save_url": f"/audio_extract/{session_id}/{safe_path}?offset={offset}&convert={int(convert)}&download=1",
            }
        )

    download = request.args.get("download", "0") in {"1", "true", "yes"}
    base_name = Path(safe_path).stem
    file_name = f"{base_name}_{offset:08x}.{out_ext}"

    return send_file(
        io.BytesIO(out_data),
        mimetype=out_mime,
        as_attachment=download,
        download_name=file_name,
    )


@app.get("/audio_stream/<session_id>/<path:raw_path>")
def audio_stream(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    data = entry["data"]
    if not isinstance(data, bytes):
        return jsonify({"error": "Некорректные данные артефакта"}), 500

    probe = detect_audio_signature(data, safe_path)
    if not probe:
        return jsonify({"error": "Музыкальная сигнатура не найдена"}), 400

    offset = int(request.args.get("offset", probe["offset"]))
    if offset < 0 or offset >= len(data):
        return jsonify({"error": "Недопустимый offset"}), 400

    mime = str(probe["mime"])
    ext = "bin"
    for fmt, meta in AUDIO_FORMAT_META.items():
        if meta["mime"] == mime:
            ext = meta["ext"]
            break
    file_name = f"{Path(safe_path).stem}.{ext}"

    return send_file(
        io.BytesIO(data[offset:]),
        mimetype=mime,
        as_attachment=False,
        download_name=file_name,
    )


@app.get("/decompile/<session_id>/<path:raw_path>")
def decompile(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    data = entry["data"]
    if not isinstance(data, bytes):
        return jsonify({"error": "Некорректные данные артефакта"}), 500

    if not safe_path.lower().endswith(".class"):
        return jsonify({"error": "Декомпиляция поддерживается только для .class"}), 400

    ok, setup_error = ensure_java_and_cfr()
    if not ok:
        return jsonify({"error": setup_error, "hex_preview": build_hex_preview(data)}), 200

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            class_path = tmp_dir / Path(safe_path).name
            class_path.write_bytes(data)

            output_dir = tmp_dir / "out"
            output_dir.mkdir(parents=True, exist_ok=True)

            process = subprocess.run(
                [
                    "java",
                    "-jar",
                    str(CFR_PATH),
                    str(class_path),
                    "--outputdir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                timeout=45,
            )

            java_files = list(output_dir.rglob("*.java"))
            if process.returncode != 0 or not java_files:
                err = (process.stderr or process.stdout or "Не удалось декомпилировать class файл").strip()
                return jsonify({"error": err, "hex_preview": build_hex_preview(data)}), 200

            java_source = java_files[0].read_text(encoding="utf-8", errors="ignore")
            return jsonify({"java_source": java_source, "hex_preview": None, "error": None}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Ошибка декомпиляции: {exc}", "hex_preview": build_hex_preview(data)}), 200


@app.get("/artifact/<session_id>/<path:raw_path>")
def artifact(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    return send_file(
        io.BytesIO(entry["data"]),
        mimetype=entry["mime"],
        as_attachment=False,
        download_name=os.path.basename(safe_path),
    )


@app.get("/download/<session_id>/<path:raw_path>")
def download(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    ext = Path(safe_path).suffix
    name = os.path.basename(safe_path)
    if not ext:
        probe = detect_audio_signature(entry["data"], safe_path) if isinstance(entry["data"], bytes) else None
        if probe:
            fmt = str(probe["format"])
            name = f"{name}.{AUDIO_FORMAT_META.get(fmt, {'ext': 'bin'})['ext']}"

    return send_file(
        io.BytesIO(entry["data"]),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=name,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
