@echo off
rem Release: release.bat 0.2.0 "notes" [--folder \\server\share\Osnastka]
cd /d "%~dp0"
.venv\Scripts\python.exe tools\release.py %*
