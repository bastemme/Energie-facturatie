@echo off
title Factuurspoor
cd /d "%~dp0"
echo.
echo  Factuurspoor wordt gestart. De eerste keer duurt dit een paar minuten...
echo.
where uv >nul 2>nul
if errorlevel 1 (
  if not exist "%USERPROFILE%\.local\bin\uv.exe" (
    echo  Benodigd hulpprogramma 'uv' wordt eenmalig geinstalleerd...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  )
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
uv run --python 3.11 python -m app.cli demo
echo.
echo  Factuurspoor is gestopt. Druk op een toets om dit venster te sluiten.
pause >nul
