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
        (os.path.join(ROOT, "nx", "export_assembly.py"), "nx"),
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
