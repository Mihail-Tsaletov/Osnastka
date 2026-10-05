@echo off
rem Сборка Osnastka.exe: результат в dist\Osnastka\ (переносить всю папку целиком)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --log-level WARN Osnastka.spec || exit /b 1
echo.
echo Done: dist\Osnastka\Osnastka.exe
