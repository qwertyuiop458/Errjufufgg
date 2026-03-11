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

from decompiler import (
    DecompilerError,
    DecompilerNotConfiguredError,
    DecompilerTimeoutError,
    decompile_class_bytes,
)

from parser import (
    ARTIFACT_STORE,
    analyze_archive,
    parse_jad,
    sanitize_rel_path,
    summarize_jad,
)

app = Flask(__name__)

CFR_URL = "https://github.com/leibnitz27/cfr/releases/download/0.152/cfr-0.152.jar"
CFR_PATH = Path(__file__).resolve().parent / "cfr.jar"


def ensure_java_and_cfr() -> tuple[bool, str | None]:
    java_ok = shutil.which("java") is not None
    if java_ok:
        java_check = subprocess.run(["java", "-version"], capture_output=True, text=True)
        java_ok = java_check.returncode == 0

    if not java_ok:
        try:
            subprocess.run(["apt-get", "update"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["apt-get", "install", "-y", "default-jdk"],
                check=True,
                capture_output=True,
                text=True,
            )
            java_ok = shutil.which("java") is not None
        except Exception as exc:  # noqa: BLE001
            return False, f"Java не установлена и автоустановка не удалась: {exc}"

    if not CFR_PATH.exists():
        try:
            urllib.request.urlretrieve(CFR_URL, CFR_PATH)
        except Exception as exc:  # noqa: BLE001
            return False, f"Не удалось скачать CFR: {exc}"

    return True, None


def build_hex_preview(data: bytes, limit: int = 256) -> str:
    chunk = data[:limit]
    lines: list[str] = []
    for i in range(0, len(chunk), 16):
        part = chunk[i : i + 16]
        hex_values = " ".join(f"{b:02x}" for b in part)
        ascii_values = "".join(chr(b) if 32 <= b <= 126 else "." for b in part)
        lines.append(f"{i:08x}  {hex_values:<47}  {ascii_values}")
    return "\n".join(lines)
HEX_LINE_WIDTH = 16
MAX_BINARY_CHUNK = 16 * 1024


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

    class_name = safe_path[:-6].replace('/', '.')

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            class_file = Path(temp_dir) / safe_path
            class_file.parent.mkdir(parents=True, exist_ok=True)
            class_file.write_bytes(entry["data"])

            cmd = ["javap", "-classpath", temp_dir, "-c", "-p", class_name]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                return jsonify({"error": "Не удалось декомпилировать", "details": proc.stderr.strip() or proc.stdout.strip()}), 500

            return jsonify({"java_source": proc.stdout})
    except FileNotFoundError:
        return jsonify({"error": "Не удалось декомпилировать", "details": "Утилита javap не установлена"}), 500
    except Exception as exc:
        return jsonify({"error": "Не удалось декомпилировать", "details": str(exc)}), 500

@app.get("/download/<session_id>/<path:raw_path>")
def download(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return jsonify({"error": "Недопустимый путь"}), 400

    if not safe_path.lower().endswith(".class"):
        return jsonify({"error": "Декомпиляция доступна только для .class файлов"}), 400

    entry = session.get(safe_path)
    if not entry:
        return jsonify({"error": "Артефакт не найден"}), 404

    return send_file(
        io.BytesIO(entry["data"]),
        mimetype=entry["mime"],
        as_attachment=False,
    try:
        java_code = decompile_class_bytes(entry["data"], safe_path)
    except DecompilerNotConfiguredError as exc:
        return jsonify({"error": str(exc)}), 503
    except DecompilerTimeoutError as exc:
        return jsonify({"error": str(exc)}), 504
    except DecompilerError as exc:
        return jsonify({"error": str(exc)}), 422

    return app.response_class(java_code, mimetype="text/plain; charset=utf-8")


@app.get("/download/<session_id>/<path:raw_path>")
def download(session_id: str, raw_path: str):
    safe_path, entry, error = get_artifact_entry(session_id, raw_path)
    if error:
        return error

    return send_file(
        io.BytesIO(entry["data"]),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=os.path.basename(safe_path),
    )


@app.get("/binary/<session_id>/<path:raw_path>")
def binary_preview(session_id: str, raw_path: str):
    _, entry, error = get_artifact_entry(session_id, raw_path)
    if error:
        return error

    offset = request.args.get("offset", default=0, type=int)
    length = request.args.get("length", default=4096, type=int)

    if offset is None or offset < 0:
        return jsonify({"error": "offset должен быть >= 0"}), 400
    if length is None or length <= 0:
        return jsonify({"error": "length должен быть > 0"}), 400

    length = min(length, MAX_BINARY_CHUNK)

    data = entry["data"]
    total_size = len(data)
    if offset > total_size:
        return jsonify({"error": "offset выходит за пределы файла"}), 400

    chunk = data[offset : offset + length]
    hex_lines: list[str] = []
    ascii_lines: list[str] = []
    for index in range(0, len(chunk), HEX_LINE_WIDTH):
        line = chunk[index : index + HEX_LINE_WIDTH]
        hex_lines.append(" ".join(f"{byte:02x}" for byte in line))
        ascii_lines.append("".join(chr(byte) if 32 <= byte <= 126 else "." for byte in line))

    return jsonify(
        {
            "offset": offset,
            "length": len(chunk),
            "total_size": total_size,
            "hex_lines": hex_lines,
            "ascii_lines": ascii_lines,
        }
    )


def get_artifact_entry(session_id: str, raw_path: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return None, None, (jsonify({"error": "Сессия не найдена"}), 404)

    safe_path = sanitize_rel_path(raw_path)
    if safe_path is None:
        return None, None, (jsonify({"error": "Недопустимый путь"}), 400)

    entry = session.get(safe_path)
    if not entry:
        return None, None, (jsonify({"error": "Артефакт не найден"}), 404)

    return safe_path, entry, None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
