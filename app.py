from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from parser import (
    ARTIFACT_STORE,
    analyze_archive,
    parse_jad,
    sanitize_rel_path,
    summarize_jad,
)

app = Flask(__name__)

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


@app.get("/artifact/<session_id>/<path:raw_path>")
def artifact(session_id: str, raw_path: str):
    safe_path, entry, error = get_artifact_entry(session_id, raw_path)
    if error:
        return error

    return send_file(
        io.BytesIO(entry["data"]),
        mimetype=entry["mime"],
        as_attachment=False,
        download_name=os.path.basename(safe_path),
    )


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
