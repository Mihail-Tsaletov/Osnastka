r"""Связь с открытым NX: кнопка в NX (журнал nx\osnastka_nx.cs рядом с программой) передаёт модель программе.

Программа при запуске пишет %LOCALAPPDATA%\Osnastka\app_launch.txt — свой pid и как её запустить
(журнал решает: передать запрос запущенной программе или запустить её) — и следит за папкой inbox:
запрос request.json от журнала → открыть модель."""
from __future__ import annotations

import json
import os
import re
import sys

APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Osnastka")
INBOX = os.path.join(APPDATA, "inbox")
REQUEST = os.path.join(INBOX, "request.json")
# журнал NX (C#) лежит в папке nx рядом с программой: dist\Osnastka\nx или <проект>\nx
APP_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
           else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOURNAL_DIR = os.path.join(APP_DIR, "nx")
JOURNAL = os.path.join(JOURNAL_DIR, "osnastka_nx.cs")
TC_PREFIX = "tc:"   # модель из Teamcenter: в сценарии вместо пути к файлу — "tc:<номер>/<ревизия>"


def is_tc(source: str) -> bool:
    return bool(source) and source.startswith(TC_PREFIX)


def safe(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "model"


def tc_export_dir(source: str) -> str:
    return os.path.join(APPDATA, "nx", "models", safe(source[len(TC_PREFIX):]))


def model_stem(source: str) -> str:
    """Короткое имя модели для имён файлов: имя .prt или номер изделия Teamcenter."""
    if is_tc(source):
        return safe(source[len(TC_PREFIX):])
    return os.path.splitext(os.path.basename(source))[0]


def model_title(source: str) -> str:
    if is_tc(source):
        return source[len(TC_PREFIX):] + " (Teamcenter)"
    return os.path.basename(source or "")


def register_app():
    """Как запустить программу и её pid — для журнала кнопки в NX (вызывается при каждом запуске).

    app_launch.txt: строки «pid», «программа», «аргументы», «рабочая папка»."""
    os.makedirs(INBOX, exist_ok=True)
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, ""
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        exe, args = (pyw if os.path.exists(pyw) else sys.executable), "-m weld_viz"
    with open(os.path.join(APPDATA, "app_launch.txt"), "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()}\n{exe}\n{args}\n{APP_DIR}\n")


def take_request() -> dict | None:
    """Забрать запрос от кнопки в NX (файл удаляется)."""
    if not os.path.exists(REQUEST):
        return None
    try:
        with open(REQUEST, encoding="utf-8") as f:
            req = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(REQUEST)
    except OSError:
        pass
    return req
