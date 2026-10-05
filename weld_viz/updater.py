"""Проверка и установка обновлений.

Источник обновлений — файл update.json рядом с Osnastka.exe (если его нет — релизы GitHub по умолчанию):
  {"github": "owner/repo"}                       релизы GitHub, в релизе файл Osnastka-<версия>.zip
  {"github": "owner/repo", "token": "..."}       то же для закрытого репозитория (токен с правом чтения)
  {"folder": "\\\\server\\share\\Osnastka"}      сетевая папка: latest.json + Osnastka-<версия>.zip

Установка (только для собранного exe): новая версия распаковывается во временную папку и запускается
с ключом --apply-update — ждёт закрытия старой, копирует себя на место установки и перезапускает программу.
При запуске из исходников (есть папка .git) обновление — git pull.

Модуль использует только стандартную библиотеку: его импортирует точка входа до загрузки Qt и VTK.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass

from . import __version__

DEFAULT_CONFIG = {"github": "Mihail-Tsaletov/Osnastka"}
ASSET_PREFIX = "Osnastka-"
EXE_NAME = "Osnastka.exe"
APPLY_FLAG = "--apply-update"
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK_DIR = os.path.join(tempfile.gettempdir(), "osnastka_update")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW для git


@dataclass
class Release:
    version: str
    url: str                 # ссылка (GitHub) или путь к zip (папка)
    notes: str = ""
    headers: dict | None = None


def parse_version(v: str) -> tuple[int, int, int]:
    nums = [int(x) for x in re.findall(r"\d+", v)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def load_config() -> dict:
    path = os.path.join(APP_DIR, "update.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    return dict(DEFAULT_CONFIG)


def source_title() -> str:
    if is_git_checkout():
        return "git (" + APP_DIR + ")"
    cfg = load_config()
    return cfg["folder"] if "folder" in cfg else "GitHub " + cfg.get("github", "?")


# ------------------------------------------------------------------ проверка
def _get_json(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def latest_release() -> Release | None:
    """Последний опубликованный релиз (без сравнения версий)."""
    cfg = load_config()
    if "folder" in cfg:
        info_path = os.path.join(cfg["folder"], "latest.json")
        if not os.path.exists(info_path):
            return None
        with open(info_path, encoding="utf-8-sig") as f:
            info = json.load(f)
        return Release(info["version"], os.path.join(cfg["folder"], info["file"]), info.get("notes", ""))

    repo = cfg.get("github")
    if not repo:
        return None
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Osnastka-updater"}
    if cfg.get("token"):
        headers["Authorization"] = "Bearer " + cfg["token"]
    try:
        data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest", headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # релизов нет (или закрытый репозиторий без токена)
        raise
    asset = next((a for a in data.get("assets", [])
                  if a["name"].startswith(ASSET_PREFIX) and a["name"].endswith(".zip")), None)
    if asset is None:
        return None
    if cfg.get("token"):  # закрытый репозиторий: скачивание через API
        url, dl_headers = asset["url"], dict(headers, Accept="application/octet-stream")
    else:
        url, dl_headers = asset["browser_download_url"], {"User-Agent": "Osnastka-updater"}
    return Release(data.get("tag_name", "").lstrip("v"), url, data.get("body") or "", dl_headers)


def check() -> Release | None:
    """Релиз новее установленной версии или None. Сетевые ошибки пробрасываются."""
    rel = latest_release()
    if rel and parse_version(rel.version) > parse_version(__version__):
        return rel
    return None


# ------------------------------------------------------------------ скачивание и установка
def download(rel: Release, progress=None) -> str:
    """Скачивает zip релиза во временную папку, возвращает путь."""
    os.makedirs(WORK_DIR, exist_ok=True)
    dest = os.path.join(WORK_DIR, f"{ASSET_PREFIX}{rel.version}.zip")
    if os.path.exists(rel.url):
        src, total = open(rel.url, "rb"), os.path.getsize(rel.url)
    else:
        resp = urllib.request.urlopen(urllib.request.Request(rel.url, headers=rel.headers or {}), timeout=30)
        src, total = resp, int(resp.headers.get("Content-Length") or 0)
    done = 0
    with src, open(dest, "wb") as out:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dest


def start_install(zip_path: str):
    """Распаковывает новую версию и запускает её установщиком. Вызывающий должен сразу завершиться."""
    if not FROZEN:
        raise RuntimeError("Установка из zip возможна только для собранной программы (Osnastka.exe)")
    stage = os.path.join(WORK_DIR, "new")
    shutil.rmtree(stage, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(stage)
    new_exe = next((os.path.join(root, EXE_NAME) for root, _, files in os.walk(stage) if EXE_NAME in files), None)
    if not new_exe:
        raise RuntimeError(f"В архиве нет {EXE_NAME}")
    subprocess.Popen([new_exe, APPLY_FLAG, APP_DIR, str(os.getpid())], close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    return str(pid) in out


def _replace_dir(src: str, dst: str):
    for attempt in range(20):  # файлы могут быть ещё заняты антивирусом/проводником
        try:
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            return
        except OSError:
            time.sleep(0.5)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def apply_update(target_dir: str, old_pid: int):
    """Выполняется НОВОЙ версией из временной папки: заменить файлы установленной и перезапустить."""
    deadline = time.time() + 60
    while _pid_alive(old_pid) and time.time() < deadline:
        time.sleep(0.5)
    time.sleep(0.5)
    my_dir = os.path.dirname(sys.executable)
    _replace_dir(os.path.join(my_dir, "_internal"), os.path.join(target_dir, "_internal"))
    for attempt in range(20):
        try:
            shutil.copy2(sys.executable, os.path.join(target_dir, EXE_NAME))
            break
        except OSError:
            time.sleep(0.5)
    subprocess.Popen([os.path.join(target_dir, EXE_NAME)], cwd=target_dir, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def handle_apply_flag(argv: list[str]) -> bool:
    """Вызывается точкой входа до загрузки интерфейса. True — это был запуск установщика."""
    if len(argv) >= 4 and argv[1] == APPLY_FLAG:
        apply_update(argv[2], int(argv[3]))
        return True
    return False


def cleanup():
    """Удаляет остатки прошлого обновления (вызывать при обычном запуске)."""
    if FROZEN and not os.path.normcase(sys.executable).startswith(os.path.normcase(WORK_DIR)):
        shutil.rmtree(WORK_DIR, ignore_errors=True)


# ------------------------------------------------------------------ запуск из исходников: git
def is_git_checkout() -> bool:
    return not FROZEN and os.path.isdir(os.path.join(APP_DIR, ".git"))


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", APP_DIR, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", creationflags=NO_WINDOW, timeout=60)


def git_check() -> int:
    """Сколько новых коммитов на сервере (0 — актуально; -1 — нет удалённого репозитория)."""
    if _git("rev-parse", "--abbrev-ref", "@{u}").returncode != 0:
        return -1
    _git("fetch", "--quiet")
    r = _git("rev-list", "--count", "HEAD..@{u}")
    return int(r.stdout.strip() or 0) if r.returncode == 0 else -1


def git_pull() -> str:
    r = _git("pull", "--ff-only")
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    return r.stdout.strip()
