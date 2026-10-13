"""Где программа хранит свои данные.

Папка с моделью (и папка программы в Program Files) может быть только для чтения, поэтому:
  * сценарии — «Документы\\Osnastka\\Сценарии» (видно пользователю, переживает переустановку программы);
  * кэш выгрузки из NX — %LOCALAPPDATA%\\Osnastka\\cache (служебное, пересоздаётся).
Сценарий к модели находится по полному пути к модели, а если модель перенесли — по имени файла.
"""
from __future__ import annotations

import ctypes
import os
import zlib

APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Osnastka")
CACHE_DIR = os.path.join(APPDATA, "cache")
SCENARIO_EXT = ".weldviz.yaml"


def documents_dir() -> str:
    """Папка «Документы» (в том числе перенаправленная на сетевой диск)."""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # CSIDL_PERSONAL
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def scenarios_dir() -> str:
    d = os.path.join(documents_dir(), "Osnastka", "Сценарии")
    os.makedirs(d, exist_ok=True)
    return d


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def model_cache_dir(prt_path: str) -> str:
    """Кэш выгрузки модели: имя файла + короткий код полного пути (одинаковые имена из разных папок)."""
    stem = os.path.splitext(os.path.basename(prt_path))[0]
    code = "%08x" % (zlib.crc32(norm(prt_path).encode("utf-8")) & 0xFFFFFFFF)
    return os.path.join(CACHE_DIR, f"{stem}_{code}")


def legacy_cache_dir(prt_path: str) -> str:
    """Где кэш лежал в версиях до 0.4 — рядом с моделью."""
    stem = os.path.splitext(os.path.basename(prt_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(prt_path)), ".weldviz", stem)


def new_scenario_path(model_stem: str) -> str:
    """Свободное имя для нового сценария в папке сценариев."""
    d = scenarios_dir()
    path = os.path.join(d, model_stem + SCENARIO_EXT)
    n = 2
    while os.path.exists(path):
        path = os.path.join(d, f"{model_stem} ({n}){SCENARIO_EXT}")
        n += 1
    return path
