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

    return send_file(
        io.BytesIO(entry["data"]),
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=os.path.basename(safe_path),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
