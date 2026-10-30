@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo run install_windows.bat first & pause & exit /b 1)
set PYTHONPATH=%~dp0src
.venv\Scripts\python.exe -m vertimeeter %*
pause
