from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path


class DecompilerError(RuntimeError):
    """Base decompilation error."""


class DecompilerNotConfiguredError(DecompilerError):
    """Raised when decompiler binaries are missing."""


class DecompilerTimeoutError(DecompilerError):
    """Raised when decompiler process times out."""


class DecompilerExecutionError(DecompilerError):
    """Raised when decompiler fails on input class."""


BASE_DIR = Path(__file__).resolve().parent
TOOLS_DIR = BASE_DIR / "tools"
JAVA_BIN = os.environ.get("JAVA_BIN", "java")
CFR_JAR = Path(os.environ.get("DECOMPILER_CFR_JAR", TOOLS_DIR / "cfr.jar"))
FERNFLOWER_JAR = Path(os.environ.get("DECOMPILER_FERNFLOWER_JAR", TOOLS_DIR / "fernflower.jar"))
DECOMPILER_TIMEOUT = int(os.environ.get("DECOMPILER_TIMEOUT", "15"))


def _pick_decompiler() -> tuple[str, Path]:
    if CFR_JAR.exists():
        return "cfr", CFR_JAR
    if FERNFLOWER_JAR.exists():
        return "fernflower", FERNFLOWER_JAR
    raise DecompilerNotConfiguredError(
        "Не найден decompiler jar. Положите tools/cfr.jar или tools/fernflower.jar."
    )


def _read_output(out_dir: Path, class_path: str) -> str:
    java_rel = Path(class_path).with_suffix(".java")
    java_file = out_dir / java_rel
    if not java_file.exists():
        candidates = sorted(out_dir.rglob("*.java"))
        if len(candidates) == 1:
            java_file = candidates[0]
        else:
            raise DecompilerExecutionError("Декомпилятор не вернул Java-файл.")
    return java_file.read_text(encoding="utf-8", errors="replace")


def decompile_class_bytes(class_bytes: bytes, class_path: str) -> str:
    decompiler_name, decompiler_jar = _pick_decompiler()
    try:
        with tempfile.TemporaryDirectory(prefix="j2me-decompile-") as tmp:
            temp_root = Path(tmp)
            in_file = temp_root / "input" / class_path
            in_file.parent.mkdir(parents=True, exist_ok=True)
            in_file.write_bytes(class_bytes)

            out_dir = temp_root / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            if decompiler_name == "cfr":
                command = [
                    JAVA_BIN,
                    "-jar",
                    str(decompiler_jar),
                    str(in_file),
                    "--silent",
                    "true",
                    "--outputdir",
                    str(out_dir),
                ]
            else:
                command = [
                    JAVA_BIN,
                    "-jar",
                    str(decompiler_jar),
                    str(in_file),
                    str(out_dir),
                ]

            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                timeout=DECOMPILER_TIMEOUT,
            )

            java_code = _read_output(out_dir, class_path)
            if not java_code.strip() and completed.stdout.strip():
                return completed.stdout
            return java_code
    except FileNotFoundError as exc:
        raise DecompilerNotConfiguredError(
            "Java Runtime не найден. Установите java и проверьте PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DecompilerTimeoutError(
            f"Декомпиляция превысила таймаут ({DECOMPILER_TIMEOUT} сек)."
        ) from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        if details:
            details = details[:400]
            raise DecompilerExecutionError(f"Ошибка декомпиляции: {details}") from exc
        raise DecompilerExecutionError(
            "Не удалось декомпилировать класс (возможно, повреждён или обфусцирован)."
        ) from exc
