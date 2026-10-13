"""Запуск NX-журнала экспорта сборки и кеш результатов."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from . import nx_link, paths

JOURNAL = nx_link.JOURNAL     # nx\osnastka_nx.cs рядом с программой
EXPORT_FORMAT = 3   # должен совпадать с FORMAT в nx/osnastka_nx.cs


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
    """Кэш выгрузки — в %LOCALAPPDATA%\\Osnastka\\cache: папка модели может быть только для чтения."""
    if nx_link.is_tc(prt_path):   # модель из Teamcenter — выгружена кнопкой в NX
        return nx_link.tc_export_dir(prt_path)
    return paths.model_cache_dir(prt_path)


def _fresh(f: str, prt_path: str) -> bool:
    try:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("format", 1) != EXPORT_FORMAT:
            return False   # выгрузка старой версией программы
        if nx_link.is_tc(prt_path):
            return True    # файла на диске нет — актуальна последняя выгрузка кнопкой в NX
        return abs(data.get("source_mtime", 0) - os.path.getmtime(prt_path)) < 1e-3
    except Exception:
        return False


def is_export_fresh(prt_path: str) -> bool:
    f = os.path.join(export_dir_for(prt_path), "model.json")
    if not os.path.exists(f) and not nx_link.is_tc(prt_path):
        # кэш версий до 0.4 рядом с моделью — один раз скопировать, чтобы не выгружать из NX заново
        legacy = paths.legacy_cache_dir(prt_path)
        if _fresh(os.path.join(legacy, "model.json"), prt_path):
            try:
                shutil.copytree(legacy, export_dir_for(prt_path), dirs_exist_ok=True)
            except OSError:
                return False
    if not os.path.exists(f):
        return False
    return _fresh(f, prt_path)


def export_command(prt_path: str) -> list[str]:
    if nx_link.is_tc(prt_path):
        raise RuntimeError("Модель из Teamcenter обновляется из NX: откройте её в NX и нажмите кнопку "
                           "«Osnastka» (или Alt+F8 → nx\\osnastka_nx.cs из папки программы → «Выполнить»).")
    exe = find_run_journal()
    if not exe:
        raise RuntimeError("Не найден NX (run_journal.exe). Проверьте переменную UGII_BASE_DIR.")
    if not os.path.exists(JOURNAL):
        raise RuntimeError(f"Не найден журнал выгрузки:\n{JOURNAL}\n\nОн должен лежать в папке nx рядом с программой.")
    return [exe, JOURNAL, "-args", os.path.abspath(prt_path), export_dir_for(prt_path)]


def export_succeeded(prt_path: str, output: str, started: float) -> bool:
    """Успех выгрузки: метка в выводе журнала или свежий model.json (вывод старых NX бывает пустым)."""
    if "EXPORT_OK" in output:
        return True
    f = os.path.join(export_dir_for(prt_path), "model.json")
    return os.path.exists(f) and os.path.getmtime(f) >= started - 1


def run_export(prt_path: str) -> str:
    """Синхронный экспорт (для командной строки). Возвращает папку экспорта."""
    started = time.time()
    out = subprocess.run(export_command(prt_path), capture_output=True, text=True, encoding="cp1251", errors="replace")
    if not export_succeeded(prt_path, out.stdout, started):
        raise RuntimeError("Экспорт из NX не удался:\n" + out.stdout[-3000:] + out.stderr[-2000:])
    return export_dir_for(prt_path)
