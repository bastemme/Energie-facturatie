@echo off
rem Read-only inspectie: klikt niet in READY en start geen export.
cd /d "%~dp0"
where py >nul 2>nul || (echo Python ontbreekt. Installeer Python 3.11 of nieuwer via python.org. & pause & exit /b 1)
if not exist .venv (py -3 -m venv .venv || (pause & exit /b 1))
call .venv\Scripts\activate.bat
python -m pip install --quiet -r requirements-inspectie.txt || (pause & exit /b 1)
python inspecteer_ready.py omgeving
echo.
python inspecteer_ready.py vensters
echo.
echo Zoek hierboven de PID van READY. Zie README.md voor de volgende stap.
cmd /k
