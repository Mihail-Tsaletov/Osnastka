@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo First-time setup...
  py -3.12 -m venv .venv || exit /b 1
  .venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m weld_viz %*
