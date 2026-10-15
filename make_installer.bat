@echo off
rem Installers: dist\Osnastka-Setup-<version>.exe and dist\Osnastka-win7-Setup-<version>.exe
rem Build the program first: build_exe.bat, build_exe_win7.bat
cd /d "%~dp0"
.venv\Scripts\python.exe tools\make_installer.py %*
