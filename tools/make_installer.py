"""Установщики: dist/Osnastka-Setup-<версия>.exe (Windows 10/11) и dist/Osnastka-win7-Setup-<версия>.exe.

  python tools/make_installer.py [--rebuild-stub]

Установщик = программа установки (installer/setup_stub.py, собирается Python 3.8 из .venv38 — запускается и
на Windows 7) + архив готовой сборки (dist/Osnastka или dist_win7/Osnastka), дописанный в конец exe,
+ 16 байт: «OSNPAYLD» и длина архива. Сначала соберите программу: build_exe.bat / build_exe_win7.bat.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STUB_SRC = os.path.join(ROOT, "installer", "setup_stub.py")
STUB_DIR = os.path.join(ROOT, "build", "installer")
STUB = os.path.join(STUB_DIR, "OsnastkaSetupStub.exe")
PY38 = os.path.join(ROOT, ".venv38", "Scripts", "pyinstaller.exe")
TRAILER = b"OSNPAYLD"
FLAVORS = (("win10", os.path.join(ROOT, "dist", "Osnastka"), "Osnastka-Setup-{v}.exe"),
           ("win7", os.path.join(ROOT, "dist_win7", "Osnastka"), "Osnastka-win7-Setup-{v}.exe"))


def ucrt_dlls() -> list[str]:
    kits = r"C:\Program Files (x86)\Windows Kits\10\Redist"
    dirs = sorted(glob.glob(os.path.join(kits, "*", "ucrt", "DLLs", "x64"))) + \
        glob.glob(os.path.join(kits, "ucrt", "DLLs", "x64"))
    return glob.glob(os.path.join(dirs[-1], "*.dll")) if dirs else []


def build_stub(force: bool = False, uac: bool = True) -> str:
    """uac=False — проверочная сборка без запроса прав администратора (ставит только для текущего пользователя)."""
    stub_dir = STUB_DIR if uac else STUB_DIR + "_nouac"
    stub = os.path.join(stub_dir, "OsnastkaSetupStub.exe")
    if os.path.exists(stub) and not force and os.path.getmtime(stub) >= os.path.getmtime(STUB_SRC):
        return stub
    if not os.path.exists(PY38):
        sys.exit("Нет .venv38 (Python 3.8 + PySide2 + PyInstaller) — установщик собирается им, чтобы работать "
                 "и на Windows 7.")
    cmd = [PY38, "--noconfirm", "--log-level", "WARN", "--onefile", "--noconsole"] + (["--uac-admin"] if uac else []) + [
           "--name", "OsnastkaSetupStub", "--icon", os.path.join(ROOT, "assets", "osnastka.ico"),
           "--distpath", stub_dir, "--workpath", os.path.join(stub_dir, "work"),
           "--specpath", os.path.join(stub_dir, "work"),
           "--exclude-module", "tkinter", "--exclude-module", "numpy"]
    for dll in ucrt_dlls():   # Windows 7 без обновлений: универсальная библиотека C рядом с python38.dll
        cmd += ["--add-binary", dll + os.pathsep + "."]
    subprocess.run(cmd + [STUB_SRC], check=True, cwd=ROOT)
    return stub


def read_version() -> str:
    with open(os.path.join(ROOT, "osnastka_build.py"), encoding="utf-8") as f:
        m = re.search(r'^VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        sys.exit("Нет VERSION в osnastka_build.py")
    return m.group(1)


def make_installer(flavor: str, app_dir: str, out_path: str, version: str, stub: str) -> str:
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("setup.json", json.dumps({"version": version, "flavor": flavor}, ensure_ascii=False))
            for root, _, files in os.walk(app_dir):
                for name in files:
                    full = os.path.join(root, name)
                    z.write(full, "app/" + os.path.relpath(full, app_dir).replace("\\", "/"))
        length = os.path.getsize(tmp)
        with open(out_path, "wb") as out:
            for src in (stub, tmp):
                with open(src, "rb") as f:
                    while True:
                        chunk = f.read(1 << 22)
                        if not chunk:
                            break
                        out.write(chunk)
            out.write(TRAILER + length.to_bytes(8, "little"))
    finally:
        os.remove(tmp)
    print("Установщик:", out_path, f"{os.path.getsize(out_path) / 2**20:.0f} МБ")
    return out_path


def make_all(out_dir: str | None = None, force_stub: bool = False, uac: bool = True) -> list[str]:
    version = read_version()
    stub = build_stub(force_stub, uac)
    out = []
    for flavor, app_dir, name in FLAVORS:
        if not os.path.exists(os.path.join(app_dir, "Osnastka.exe")):
            print(f"! Нет сборки {app_dir} — установщик {flavor} пропущен")
            continue
        out.append(make_installer(flavor, app_dir, os.path.join(out_dir or os.path.join(ROOT, "dist"),
                                                                name.format(v=version)), version, stub))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Установщики Osnastka")
    ap.add_argument("--rebuild-stub", action="store_true", help="пересобрать программу установки")
    ap.add_argument("--out", help="папка для установщиков (по умолчанию dist)")
    ap.add_argument("--no-uac", action="store_true", help="проверочная сборка без запроса прав администратора")
    a = ap.parse_args()
    make_all(a.out, a.rebuild_stub, uac=not a.no_uac)
