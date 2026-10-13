# -*- mode: python ; coding: utf-8 -*-
# Сборка: build_exe.bat  (или: .venv\Scripts\pyinstaller --noconfirm Osnastka.spec)
# Результат: dist\Osnastka\Osnastka.exe (папка целиком переносится на другой компьютер)
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = SPECPATH
CONSOLE = os.environ.get("OSNASTKA_CONSOLE") == "1"   # отладочная сборка с окном консоли

a = Analysis(
    [os.path.join(ROOT, "osnastka.py")],
    pathex=[ROOT],
    datas=[
        (os.path.join(ROOT, "assets", "osnastka.png"), "assets"),
    ] + collect_data_files("pyvista"),
    hiddenimports=collect_submodules("vtkmodules") + ["pyvistaqt", "qtpy"],
    excludes=["tkinter", "IPython", "PyQt5", "PyQt6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Osnastka",
    console=CONSOLE,
    icon=os.path.join(ROOT, "assets", "osnastka.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Osnastka", upx=False)

# журнал NX (C#) — рядом с Osnastka.exe, в папке nx: программа берёт его оттуда, его же выбирают для кнопки в NX
import shutil  # noqa: E402
_nx_out = os.path.join(DISTPATH, "Osnastka", "nx")
os.makedirs(_nx_out, exist_ok=True)
shutil.copy2(os.path.join(ROOT, "nx", "osnastka_nx.cs"), _nx_out)

# сборка для Windows 7 (собирается Python 3.8): универсальная библиотека C (UCRT) рядом с exe —
# на Windows 7 без обновлений её нет (официальное локальное развёртывание Microsoft)
import glob  # noqa: E402
import sys  # noqa: E402

if sys.version_info < (3, 9):
    kits = r"C:\Program Files (x86)\Windows Kits\10\Redist"
    dirs = sorted(glob.glob(os.path.join(kits, "*", "ucrt", "DLLs", "x64"))) + \
        glob.glob(os.path.join(kits, "ucrt", "DLLs", "x64"))
    if not dirs:
        raise SystemExit("Нет UCRT (Windows 10 SDK, Redist\\ucrt\\DLLs\\x64) — нужен для сборки под Windows 7")
    for dll in glob.glob(os.path.join(dirs[-1], "*.dll")):
        shutil.copy2(dll, os.path.join(DISTPATH, "Osnastka"))
