"""Обновление программы без прав администратора и без запуска других программ.

Exe после установки не меняется (папка программы — только для чтения). Обновляется код: архив
Osnastka-update-<версия>.zip (код программы + журнал NX + update.json) распаковывается в
%LOCALAPPDATA%\\Osnastka\\app\\v<версия>, в app\\current.json записывается, какой код запускать.
При следующем запуске загрузчик exe (osnastka.py) подключает этот код. Перезапуск — самой программой.

Откуда берутся архивы:
  * папка обновлений (по умолчанию «Документы\\Osnastka\\Обновления»; её можно сменить) — сюда кладут архив
    с флешки или из сетевой папки;
  * релизы GitHub (если есть доступ в интернет) — тот же архив Osnastka-update-<версия>.zip.
Архив один для сборок Windows 10/11 и Windows 7. Если коду нужны новые библиотеки (runtime больше,
чем у установленного exe), установка не выполняется — нужна переустановка программы администратором.

Запуск из исходников (есть папка .git) — обновление через git pull.
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

GITHUB_REPO = "Mihail-Tsaletov/Osnastka"
PACKAGE_RE = re.compile(r"^Osnastka-update-(\d+(?:\.\d+){0,2})\.zip$", re.IGNORECASE)
FROZEN = getattr(sys, "frozen", False)
APP_DIR = os.path.dirname(sys.executable) if FROZEN else \
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Osnastka")
CODE_ROOT = os.path.join(APPDATA, "app")
CURRENT = os.path.join(CODE_ROOT, "current.json")
SETTINGS = os.path.join(APPDATA, "settings.json")
WORK_DIR = os.path.join(tempfile.gettempdir(), "osnastka_update")
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW для git


@dataclass
class Package:
    version: str
    path: str                 # путь к zip (папка) или ссылка (GitHub)
    notes: str = ""
    runtime: int = 1
    source: str = "папка"


def parse_version(v: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def build_info() -> tuple[str, int]:
    """(версия встроенного кода, runtime) установленного exe."""
    try:
        import osnastka_build
        return osnastka_build.VERSION, int(osnastka_build.RUNTIME)
    except Exception:
        return __version__, 1


def running_from_update() -> bool:
    return bool(os.environ.get("OSNASTKA_CODE_DIR"))


# ------------------------------------------------------------------ настройки
def _settings() -> dict:
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_settings(s: dict):
    os.makedirs(APPDATA, exist_ok=True)
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=1)


def update_folder() -> str:
    folder = _settings().get("update_folder")
    if not folder:
        from .paths import documents_dir
        folder = os.path.join(documents_dir(), "Osnastka", "Обновления")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return folder


def set_update_folder(folder: str):
    s = _settings()
    s["update_folder"] = folder
    _save_settings(s)


# ------------------------------------------------------------------ поиск архивов
def read_package(path: str) -> Package | None:
    """Сведения об архиве обновления (update.json внутри) или None, если это не архив обновления."""
    try:
        with zipfile.ZipFile(path) as z:
            info = json.loads(z.read("update.json").decode("utf-8-sig"))
            if "weld_viz/__init__.py" not in z.namelist():
                return None
        return Package(str(info["version"]), path, info.get("notes", ""), int(info.get("runtime", 1)))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def folder_packages(folder: str | None = None) -> list[Package]:
    folder = folder or update_folder()
    out = []
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if PACKAGE_RE.match(name):
            pkg = read_package(os.path.join(folder, name))
            if pkg:
                out.append(pkg)
    out.sort(key=lambda p: parse_version(p.version), reverse=True)
    return out


def github_package() -> Package | None:
    """Архив обновления последнего релиза GitHub (сетевые ошибки пробрасываются)."""
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "Osnastka-updater"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    asset = next((a for a in data.get("assets", []) if PACKAGE_RE.match(a["name"])), None)
    if not asset:
        return None
    return Package(data.get("tag_name", "").lstrip("v"), asset["browser_download_url"], data.get("body") or "",
                   source="GitHub")


def check(use_github: bool = True) -> Package | None:
    """Самое свежее обновление новее работающего кода: из папки, затем (если доступен) с GitHub."""
    current = parse_version(__version__)
    best = next((p for p in folder_packages() if parse_version(p.version) > current), None)
    if use_github and best is None:
        try:
            gh = github_package()
            if gh and parse_version(gh.version) > current:
                best = gh
        except Exception:
            pass   # нет интернета / GitHub закрыт — обычная ситуация на рабочих ПК
    return best


# ------------------------------------------------------------------ установка
def download(pkg: Package, progress=None) -> str:
    """Скачать архив с GitHub во временную папку (архив из папки возвращается как есть)."""
    if os.path.exists(pkg.path):
        return pkg.path
    os.makedirs(WORK_DIR, exist_ok=True)
    dest = os.path.join(WORK_DIR, f"Osnastka-update-{pkg.version}.zip")
    resp = urllib.request.urlopen(urllib.request.Request(pkg.path, headers={"User-Agent": "Osnastka-updater"}),
                                  timeout=30)
    total, done = int(resp.headers.get("Content-Length") or 0), 0
    with resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dest


def install(zip_path: str) -> Package:
    """Распаковать архив обновления в профиль пользователя и сделать его кодом для следующего запуска."""
    pkg = read_package(zip_path)
    if pkg is None:
        raise RuntimeError("Это не архив обновления программы (нет update.json или кода программы).")
    _, runtime = build_info()
    if pkg.runtime > runtime:
        raise RuntimeError(f"Версии {pkg.version} нужны новые библиотеки программы — её нужно установить заново "
                           f"(с правами администратора), обновлением кода тут не обойтись.")
    os.makedirs(CODE_ROOT, exist_ok=True)
    name = "v" + pkg.version
    final, stage = os.path.join(CODE_ROOT, name), os.path.join(CODE_ROOT, name + ".tmp")
    shutil.rmtree(stage, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(stage)
    if not os.path.isfile(os.path.join(stage, "weld_viz", "__init__.py")):
        shutil.rmtree(stage, ignore_errors=True)
        raise RuntimeError("В архиве нет кода программы.")
    shutil.rmtree(final, ignore_errors=True)
    os.replace(stage, final)
    with open(CURRENT, "w", encoding="utf-8") as f:
        json.dump({"version": pkg.version, "dir": name, "runtime": pkg.runtime,
                   "installed": time.strftime("%Y-%m-%d %H:%M:%S"), "from": zip_path}, f, ensure_ascii=False, indent=1)
    _prune(keep={name, _running_dir_name()})
    return pkg


def revert_to_builtin():
    """Отключить установленные обновления: со следующего запуска — код, встроенный в exe."""
    try:
        os.remove(CURRENT)
    except OSError:
        pass


def _running_dir_name() -> str:
    d = os.environ.get("OSNASTKA_CODE_DIR")
    return os.path.basename(d) if d else ""


def _prune(keep: set):
    """Удалить старые версии кода (оставить текущую и работающую сейчас)."""
    try:
        for name in os.listdir(CODE_ROOT):
            if name.startswith("v") and name not in keep and os.path.isdir(os.path.join(CODE_ROOT, name)):
                shutil.rmtree(os.path.join(CODE_ROOT, name), ignore_errors=True)
    except OSError:
        pass


def restart_command() -> list[str]:
    """Как перезапустить программу: тот же exe (другие программы не запускаются)."""
    return [sys.executable] if FROZEN else [sys.executable, "-m", "weld_viz"]


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
