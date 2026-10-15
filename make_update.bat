@echo off
rem Code update archive: dist\Osnastka-update-<version>.zip (for installs without write access)
cd /d "%~dp0"
.venv\Scripts\python.exe tools\make_update.py %*
