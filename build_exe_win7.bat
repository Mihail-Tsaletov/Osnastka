@echo off
rem Build for Windows 7: Python 3.8 + PySide2 (Qt 5.15) + VTK 9.2 -> dist_win7\Osnastka\
cd /d "%~dp0"
if not exist .venv38\Scripts\python.exe (
  echo Need .venv38: portable Python 3.8 in .py38 + venv with requirements_win7.txt
  exit /b 1
)
.venv38\Scripts\pyinstaller --noconfirm --log-level WARN --distpath dist_win7 --workpath build\work38 Osnastka.spec || exit /b 1
echo.
echo Done: dist_win7\Osnastka\Osnastka.exe
