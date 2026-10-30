@echo off
rem VertiMeeter '98 - Windows installer. Creates .venv and installs the Python requirements.
rem Needs Python 3.10+ (python.org, tick "Add to PATH"). The engine is built separately (build_engine_windows.bat)
rem or downloaded from the GitHub Actions artifact. See README_WINDOWS.md.
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo [VertiMeeter] [ERROR]: python not found in PATH & exit /b 1)
if not exist .venv\Scripts\python.exe (
    echo [VertiMeeter] [INFO]: creating venv
    python -m venv .venv || exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
if not exist bin mkdir bin
if not exist bin\vertimeeter-engine.exe echo [VertiMeeter] [WARN]: bin\vertimeeter-engine.exe missing - run build_engine_windows.bat
if not exist bin\ffmpeg.exe echo [VertiMeeter] [WARN]: bin\ffmpeg.exe missing - the media player needs it (https://www.gyan.dev/ffmpeg/builds/, copy ffmpeg.exe into bin\)
echo [VertiMeeter] [OK]: done. Launch with vertimeeter.bat
