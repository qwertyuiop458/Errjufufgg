from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
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
ANALYSIS_LOCK = threading.Lock()


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

def build_hex_preview(data: bytes, limit: int = 256) -> str:
    chunk = data[:limit]
    lines: list[str] = []
    for i in range(0, len(chunk), 16):
        part = chunk[i : i + 16]
        hex_values = " ".join(f"{b:02x}" for b in part)
        ascii_values = "".join(chr(b) if 32 <= b <= 126 else "." for b in part)
        lines.append(f"{i:08x}  {hex_values:<47}  {ascii_values}")
    return "\n".join(lines)


def _list_session_files(session: dict) -> list[tuple[str, dict]]:
    return [(path, entry) for path, entry in session.items() if isinstance(entry, dict) and "data" in entry]


def _extract_class_name(path: str, source: str) -> str:
    match = re.search(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)", source)
    if match:
        return match.group(1)
    return Path(path).stem


def _run_full_analysis(session_id: str) -> None:
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return

    analysis = session.get("full_analysis", {})
    class_entries = [(p, e) for p, e in _list_session_files(session) if p.lower().endswith(".class")]
    analysis.update({"status": "running", "message": "Подготовка к декомпиляции...", "total": len(class_entries), "done": 0})
    session["full_analysis"] = analysis

    ok, setup_error = ensure_java_and_cfr()
    if not ok:
        analysis.update({"status": "error", "message": setup_error or "Не удалось подготовить Java/CFR"})
        return

    decompiled: list[dict[str, str]] = []
    all_sources: list[str] = []
    for idx, (path, entry) in enumerate(class_entries, start=1):
        analysis.update({"message": f"Анализ классов {idx}/{len(class_entries)}...", "done": idx - 1})
        data = entry.get("data")
        if not isinstance(data, bytes):
            continue

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                class_path = tmp_dir / Path(path).name
                class_path.write_bytes(data)
                out_dir = tmp_dir / "out"
                out_dir.mkdir(parents=True, exist_ok=True)
                process = subprocess.run(
                    ["java", "-jar", str(CFR_PATH), str(class_path), "--outputdir", str(out_dir)],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                java_files = list(out_dir.rglob("*.java"))
                if process.returncode != 0 or not java_files:
                    decompiled.append({"path": path, "error": (process.stderr or process.stdout or "decompile error").strip()})
                    continue
                source = java_files[0].read_text(encoding="utf-8", errors="ignore")
                decompiled.append({"path": path, "source": source, "class_name": _extract_class_name(path, source)})
                all_sources.append(source)
        except Exception as exc:  # noqa: BLE001
            decompiled.append({"path": path, "error": str(exc)})

        analysis["done"] = idx

    analysis["message"] = "Поиск ресурсов..."
    text_blob = "\n".join(all_sources)
    lower_blob = text_blob.lower()

    main_classes = [item["class_name"] for item in decompiled if "source" in item and re.search(r"extends\s+MIDlet\b", item["source"])]
    game_canvas_classes = [item["class_name"] for item in decompiled if "source" in item and re.search(r"extends\s+(GameCanvas|Canvas)\b", item["source"])]

    def classes_by_keywords(words: tuple[str, ...]) -> list[str]:
        return sorted({item["class_name"] for item in decompiled if "source" in item and any(w in item["class_name"].lower() for w in words)})

    player_classes = classes_by_keywords(("player", "hero"))
    enemy_classes = classes_by_keywords(("enemy", "monster"))
    bullet_classes = classes_by_keywords(("bullet", "shot"))
    item_classes = classes_by_keywords(("item", "bonus"))

    game_type = "Unknown"
    if re.search(r"tile\s*\[\s*\]\s*\[\s*\]", text_blob) or "loadmap(" in lower_blob:
        game_type = "Platformer / Аркада"
    elif "generatemaze(" in lower_blob:
        game_type = "Лабиринт"
    elif any(token in lower_blob for token in ["vehicle", "car", "turnleft", "turnright"]):
        game_type = "Гонки"
    elif "cell[][]" in lower_blob or "board" in lower_blob:
        game_type = "Головоломка / Стратегия"

    control = "Не определено"
    if any(token in text_blob for token in ["KEY_NUM2", "KEY_NUM4", "KEY_NUM6", "KEY_NUM8"]):
        control = "4 кнопки (2/4/6/8)"
    elif "getGameAction" in text_blob:
        control = "Джойстик"
    elif "pointerPressed" in text_blob:
        control = "Сенсор"

    level_files = [p for p, _ in _list_session_files(session) if re.search(r"(^|/)(m\d{1,2}|level\d{1,2})(\.[^/]+)?$", p, re.IGNORECASE)]
    sprite_files = [p for p, e in _list_session_files(session) if re.search(r"\.(png|jpg|jpeg)$", p, re.IGNORECASE) and isinstance(e.get("data"), bytes)]
    music_files = [p for p, _ in _list_session_files(session) if re.search(r"\.(mid|midi|wav|mp3)$", p, re.IGNORECASE)]
    font_files = [p for p, _ in _list_session_files(session) if re.search(r"\.(fnt|png)$", p, re.IGNORECASE) and "font" in p.lower()]

    binary_notes: list[dict[str, str]] = []
    for p, e in _list_session_files(session):
        if re.search(r"(^|/)(m\d+|data)(\.[^/]+)?$", p, re.IGNORECASE):
            payload = e.get("data")
            if isinstance(payload, bytes):
                hex_line = " ".join(f"{b:02x}" for b in payload[:32])
                guess = "Сырые бинарные данные"
                if len(payload) % (20 * 15) == 0:
                    guess = "Похоже на tilemap 20x15 (1 байт = tile ID)"
                binary_notes.append({"file": p, "guess": guess, "first_32": hex_line})

    methods = {
        "paint(Graphics g)": bool(re.search(r"\bpaint\s*\(\s*Graphics", text_blob)),
        "keyPressed/keyReleased": bool(re.search(r"\bkeyPressed\s*\(|\bkeyReleased\s*\(", text_blob)),
        "run()/gameLoop()": bool(re.search(r"\brun\s*\(|\bgameLoop\s*\(", text_blob)),
        "loadLevel()/init()": bool(re.search(r"\bloadLevel\s*\(|\binit\s*\(", text_blob)),
    }

    class_names = [item["class_name"] for item in decompiled if "class_name" in item]
    graph_nodes = [{"id": name, "label": name} for name in sorted(set(class_names))]
    edges: set[tuple[str, str]] = set()
    for item in decompiled:
        if "source" not in item:
            continue
        src = item["class_name"]
        for dst in class_names:
            if dst == src:
                continue
            if re.search(rf"\b{re.escape(dst)}\b", item["source"]):
                edges.add((src, dst))
    graph_edges = [{"from": a, "to": b} for a, b in sorted(edges)]

    pseudocode = """while (running) {
    updatePlayer();
    updateEnemies();
    checkCollisions();
    repaint();
    sleep(30ms);
}"""

    report = {
        "game_type": game_type,
        "resolution": "Не определено",
        "control": control,
        "levels_found": len(level_files),
        "player_classes": player_classes,
        "enemy_classes": enemy_classes,
        "bullet_classes": bullet_classes,
        "item_classes": item_classes,
        "main_midlet_classes": main_classes,
        "game_canvas_classes": game_canvas_classes,
        "method_presence": methods,
        "resources": {"sprites": sprite_files, "music": music_files, "fonts": font_files},
        "level_files": level_files,
        "binary_notes": binary_notes,
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "pseudocode": pseudocode,
        "decompiled_sources": [item for item in decompiled if "source" in item],
    }

    analysis.update({"status": "done", "message": "Анализ завершён", "done": len(class_entries), "result": report})


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
    return send_file(
        io.BytesIO(data[offset:]),
        mimetype=mime,
        as_attachment=False,
        download_name=os.path.basename(safe_path),
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


@app.post("/full_analysis/start/<session_id>")
def full_analysis_start(session_id: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404

    with ANALYSIS_LOCK:
        current = session.get("full_analysis")
        if isinstance(current, dict) and current.get("status") == "running":
            return jsonify({"ok": True, "already_running": True})
        session["full_analysis"] = {"status": "running", "message": "Запуск...", "done": 0, "total": 0}
        threading.Thread(target=_run_full_analysis, args=(session_id,), daemon=True).start()
    return jsonify({"ok": True, "already_running": False})


@app.get("/full_analysis/status/<session_id>")
def full_analysis_status(session_id: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404
    data = session.get("full_analysis")
    if not isinstance(data, dict):
        return jsonify({"status": "idle", "message": "Анализ не запускался", "done": 0, "total": 0})
    return jsonify({
        "status": data.get("status", "idle"),
        "message": data.get("message", ""),
        "done": data.get("done", 0),
        "total": data.get("total", 0),
    })


@app.get("/full_analysis/result/<session_id>")
def full_analysis_result(session_id: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404
    data = session.get("full_analysis")
    if not isinstance(data, dict) or data.get("status") != "done":
        return jsonify({"error": "Анализ ещё не завершён"}), 400
    return jsonify(data.get("result", {}))


@app.get("/full_analysis/export/<session_id>")
def full_analysis_export(session_id: str):
    session = ARTIFACT_STORE.get(session_id)
    if not session:
        return jsonify({"error": "Сессия не найдена"}), 404
    data = session.get("full_analysis")
    if not isinstance(data, dict) or data.get("status") != "done":
        return jsonify({"error": "Анализ ещё не завершён"}), 400

    report = data.get("result", {})
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in report.get("decompiled_sources", []):
            path = item.get("path", "Unknown.class")
            java_path = f"src/{Path(path).with_suffix('.java').as_posix()}"
            zf.writestr(java_path, item.get("source", ""))

        for path, entry in _list_session_files(session):
            payload = entry.get("data")
            if not isinstance(payload, bytes):
                continue
            lower = path.lower()
            if re.search(r"(^|/)(m\d+|level\d+|data)(\.[^/]+)?$", lower):
                zf.writestr(f"res/levels/{Path(path).name}", payload)
            elif lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".wbmp")):
                zf.writestr(f"res/gfx/{Path(path).name}", payload)
            elif lower.endswith((".mid", ".midi", ".wav", ".mp3", ".amr")):
                zf.writestr(f"res/snd/{Path(path).name}", payload)

        analysis_text = [
            f"Тип игры: {report.get('game_type', 'Unknown')}",
            f"Управление: {report.get('control', 'Не определено')}",
            f"Уровней найдено: {report.get('levels_found', 0)}",
            f"Классы игрока: {', '.join(report.get('player_classes', [])) or '-'}",
            f"Классы врагов: {', '.join(report.get('enemy_classes', [])) or '-'}",
        ]
        zf.writestr("docs/analysis.txt", "\n".join(analysis_text))
        zf.writestr("docs/pseudocode.txt", report.get("pseudocode", ""))

    blob.seek(0)
    return send_file(blob, mimetype="application/zip", as_attachment=True, download_name="reconstructed_project.zip")


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
