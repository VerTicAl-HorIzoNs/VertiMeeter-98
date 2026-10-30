@echo off
rem VertiMeeter '98 launcher (no console window). Use vertimeeter_console.bat to see errors.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (echo run install_windows.bat first & pause & exit /b 1)
set PYTHONPATH=%~dp0src
start "" .venv\Scripts\pythonw.exe -m vertimeeter %*
