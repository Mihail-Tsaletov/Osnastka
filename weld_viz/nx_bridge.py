"""Запуск NX-журнала экспорта сборки и кеш результатов."""
from __future__ import annotations

import json
import os
import subprocess
import sys

# в собранном exe файлы лежат в sys._MEIPASS, при запуске из исходников — в корне проекта
_BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOURNAL = os.path.join(_BASE, "nx", "export_assembly.py")


def find_run_journal() -> str | None:
    base = os.environ.get("UGII_BASE_DIR")
    candidates = []
    if base:
        candidates.append(os.path.join(base, "NXBIN", "run_journal.exe"))
    siemens = r"C:\Program Files\Siemens"
    if os.path.isdir(siemens):
        for d in sorted(os.listdir(siemens), reverse=True):
            candidates.append(os.path.join(siemens, d, "NXBIN", "run_journal.exe"))
    return next((c for c in candidates if os.path.exists(c)), None)


def export_dir_for(prt_path: str) -> str:
    stem = os.path.splitext(os.path.basename(prt_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(prt_path)), ".weldviz", stem)


def is_export_fresh(prt_path: str) -> bool:
    f = os.path.join(export_dir_for(prt_path), "model.json")
    if not os.path.exists(f):
        return False
    try:
        with open(f, encoding="utf-8") as fh:
            mtime = json.load(fh).get("source_mtime", 0)
        return abs(mtime - os.path.getmtime(prt_path)) < 1e-3
    except Exception:
        return False


def export_command(prt_path: str) -> list[str]:
    exe = find_run_journal()
    if not exe:
        raise RuntimeError("Не найден NX (run_journal.exe). Проверьте переменную UGII_BASE_DIR.")
    return [exe, JOURNAL, "-args", os.path.abspath(prt_path), export_dir_for(prt_path)]


def run_export(prt_path: str) -> str:
    """Синхронный экспорт (для командной строки). Возвращает папку экспорта."""
    out = subprocess.run(export_command(prt_path), capture_output=True, text=True, encoding="cp1251", errors="replace")
    if "EXPORT_OK" not in out.stdout:
        raise RuntimeError("Экспорт из NX не удался:\n" + out.stdout[-3000:] + out.stderr[-2000:])
    return export_dir_for(prt_path)
