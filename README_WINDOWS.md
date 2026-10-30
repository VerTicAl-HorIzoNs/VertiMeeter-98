# VertiMeeter '98 for Windows (0.12.0, experimental)

Written and tested on Linux only. The Windows-specific code has never been compiled or run on Windows. Treat first build as a bring-up.

## Setup
1. Install Python 3.10+ (tick "Add to PATH") and Rust (rustup, MSVC toolchain + VS Build Tools).
2. `install_windows.bat` (venv + PyQt6, numpy).
3. `build_engine_windows.bat` (or download the `vertimeeter-engine-windows` artifact from the GitHub Actions workflow and put the exe in `bin\`).
4. Put `ffmpeg.exe` in `bin\` (media player). Optional: `opus.dll` (VBAN/OBAN opus), `fluidsynth.exe` (MIDI files).
5. `vertimeeter.bat` (or `vertimeeter_console.bat` to see errors). Config: `%APPDATA%\VertiMeeter`.

## How it differs from Linux
- Hardware strips / A buses: pick WASAPI devices; `@default` follows the Windows default.
- Virtual strips: pick a capture device, a "Loopback: <playback device>" (captures what that device plays), or a virtual cable you installed yourself. B buses: pick any playback device (e.g. a cable input).
- No built-in virtual devices (needs a signed driver), no per-app routing, no JACK patch bay, EXT/RET loops, or CD playback.
- Shared-mode WASAPI: expect about 40 ms+ per side. Adjust in Engine > System settings.
- Hotkeys use a low-level keyboard hook; MIDI uses winmm.

## Tests (Linux sandbox, fake-Windows mode)
Build the soft engine: `cd engine && cargo build --release --no-default-features --features cpal-backend --target-dir target-win`, then run `tests/test_winengine.py` and `tests/test_windows.py`.

## Not verified
Real WASAPI devices and loopback, device hot-unplug, clock drift in practice, hotkey hook, winmm MIDI, job object, opus.dll loading, MMCSS, audio quality by ear.
