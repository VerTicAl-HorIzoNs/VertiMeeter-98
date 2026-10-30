@echo off
rem Builds the Windows engine (WASAPI via cpal, no JACK) and copies it to bin\.
rem Needs Rust (https://rustup.rs) with the MSVC toolchain + Visual Studio Build Tools.
setlocal
cd /d "%~dp0engine"
where cargo >nul 2>nul || (echo [VertiMeeter] [ERROR]: cargo not found - install Rust from https://rustup.rs & exit /b 1)
cargo build --release --no-default-features --features cpal-backend || exit /b 1
if not exist ..\bin mkdir ..\bin
copy /y target\release\vertimeeter-engine.exe ..\bin\ >nul
echo [VertiMeeter] [OK]: bin\vertimeeter-engine.exe built
echo [VertiMeeter] [INFO]: optional: put opus.dll (for VBAN/OBAN opus codecs) and fluidsynth.exe next to it in bin\
