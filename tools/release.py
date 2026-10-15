"""Выпуск новой версии программы.

  release.bat 0.2.0 "Что нового"                       — релиз на GitHub (нужен gh auth login и git remote)
  release.bat 0.2.0 "Что нового" --folder \\\\srv\\Osnastka — релиз в сетевую папку (latest.json + zip)

Шаги: проверка чистого рабочего дерева → версия в weld_viz/__init__.py → сборка exe →
dist/Osnastka-<версия>.zip → коммит «Версия X» и тег vX → публикация (GitHub или папка).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "weld_viz", "__init__.py")
DIST = os.path.join(ROOT, "dist")


def run(*cmd, check=True, capture=False):
    print(">", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        sys.exit(f"Ошибка: {' '.join(cmd)}\n{(r.stdout or '') + (r.stderr or '')}")
    return r


def make_zip(app_dir: str, zip_path: str) -> str:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(app_dir):
            for name in files:
                full = os.path.join(root, name)
                z.write(full, os.path.join("Osnastka", os.path.relpath(full, app_dir)))
    print("Архив:", zip_path, f"{os.path.getsize(zip_path) / 2**20:.0f} МБ")
    return zip_path


def main():
    ap = argparse.ArgumentParser(description="Выпуск версии Osnastka")
    ap.add_argument("version", help="номер версии, например 0.2.0")
    ap.add_argument("notes", nargs="?", default="", help="что нового (покажется пользователям)")
    ap.add_argument("--folder", help="сетевая папка для обновлений вместо GitHub")
    a = ap.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        sys.exit("Версия должна быть вида 1.2.3")
    tag = "v" + a.version
    if run("git", "status", "--porcelain", capture=True).stdout.strip():
        sys.exit("Есть незакоммиченные изменения — закоммитьте их перед выпуском версии.")
    if run("git", "tag", "-l", tag, capture=True).stdout.strip():
        sys.exit(f"Тег {tag} уже существует.")
    if not a.folder:
        if not run("git", "remote", capture=True).stdout.strip():
            sys.exit("Нет git remote. Создайте репозиторий на GitHub (gh repo create) или используйте --folder.")
        if run("gh", "auth", "status", check=False, capture=True).returncode != 0:
            sys.exit("GitHub CLI не авторизован: выполните gh auth login.")

    # версия
    with open(INIT, encoding="utf-8") as f:
        text = f.read()
    with open(INIT, "w", encoding="utf-8") as f:
        f.write(re.sub(r'__version__ = "[^"]*"', f'__version__ = "{a.version}"', text))
    build_py = os.path.join(ROOT, "osnastka_build.py")   # версия встроенного в exe кода — для загрузчика
    with open(build_py, encoding="utf-8") as f:
        text = f.read()
    with open(build_py, "w", encoding="utf-8") as f:
        f.write(re.sub(r'VERSION = "[^"]*"', f'VERSION = "{a.version}"', text))

    # сборка и архив (обычная — Windows 10/11)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN", "Osnastka.spec")
    zip_path = make_zip(os.path.join(DIST, "Osnastka"), os.path.join(DIST, f"Osnastka-{a.version}.zip"))
    zips = [zip_path]
    # сборка для Windows 7 (Python 3.8 + Qt 5) — если есть окружение .venv38
    py38 = os.path.join(ROOT, ".venv38", "Scripts", "python.exe")
    win7_zip = None
    if os.path.exists(py38):
        dist7 = os.path.join(ROOT, "dist_win7")
        run(py38, "-m", "PyInstaller", "--noconfirm", "--log-level", "WARN", "--distpath", dist7,
            "--workpath", os.path.join(ROOT, "build", "work38"), "Osnastka.spec")
        win7_zip = make_zip(os.path.join(dist7, "Osnastka"), os.path.join(DIST, f"Osnastka-win7-{a.version}.zip"))
        zips.append(win7_zip)
    else:
        print("! Нет .venv38 — сборка для Windows 7 пропущена")
    # архив обновления кода — для программ, установленных без права на запись (обновление из папки)
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from make_update import make_update
    zips.append(make_update(DIST, a.notes))
    # установщики (Program Files, ярлыки, удаление) — для первой установки с правами администратора
    from make_installer import make_all
    zips += make_all(DIST)

    # коммит и тег
    run("git", "commit", "-q", "-am", f"Версия {a.version}" + (f"\n\n{a.notes}" if a.notes else ""))
    run("git", "tag", "-a", tag, "-m", f"Версия {a.version}")

    if a.folder:
        os.makedirs(a.folder, exist_ok=True)
        for z in zips:
            shutil.copy2(z, a.folder)
        info = {"version": a.version, "file": os.path.basename(zip_path), "notes": a.notes}
        if win7_zip:
            info["file_win7"] = os.path.basename(win7_zip)
        with open(os.path.join(a.folder, "latest.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        if run("git", "remote", capture=True).stdout.strip():
            run("git", "push", "--follow-tags", check=False)
        print(f"Готово: версия {a.version} выложена в {a.folder}")
    else:
        run("git", "push", "--follow-tags")
        run("gh", "release", "create", tag, *zips, "--title", f"Osnastka {a.version}",
            "--notes", a.notes or f"Версия {a.version}")
        print(f"Готово: релиз {tag} опубликован на GitHub")


if __name__ == "__main__":
    main()
