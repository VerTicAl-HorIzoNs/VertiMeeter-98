# VT-Audio Software / VertiMeeter - handoff (written 2026-10-24, end of a long chat)

Read RULES.md first, then this file. Then CHANGELOG.md and README.md in the project folder.

## 1. What this is
- VT-Audio Software = VertiMeeter's umbrella project: a suite of legally distinct Linux clones of VB-Audio software. Owner: Vertical (user `vertical`).
- VertiMeeter '98 = the Voicemeeter Potato replacement for Linux (PipeWire). Native Rust engine + PyQt6 GUI, Win9x dark purple "vertical" theme.
- Only VertiMeeter exists so far. Ideas for other suite parts (not started): VB-Cable-like virtual device (easy on PipeWire), Matrix-like routing grid, Macro Buttons-like triggers.
- Current version: 0.11.2 (`config.py` VERSION and `engine/Cargo.toml` agree). Rebuild the engine (`cargo build --release` in `engine/`) after pulling 0.11.0, it changed.

## 2. Where things are (his machine, Arch Linux)
- Project root: `~/Documents/scripts...malware...linux/_CLAUDE_/_FOR_VERTICAL_/VertiMeeter/`
- `src/vertimeeter/` python GUI | `engine/` Rust engine (`cargo build --release`) | `tests/` | `dist/` release zips | `docs/` (OBAN_PACKET_FORMAT.md) | `assets/` (vm98.png, vflac.svg/png) | `_shots/` screenshots | `.venv/` | `_old/`
- Launch: `./vertimeeter.sh`. Install: `./install.sh` (needs `sudo pacman -S opus`).
- Real engine socket: `/run/user/1000/vertimeeter-engine.sock`. His real instance may be running: never kill it, never run cleanup that touches it.
- All work on his files goes through the desktop-commander tools (start_process, read_file, write_file, edit_block). The sandbox Edit tool cannot reach his files. For big edits, write a python patch script (exact-string replace with a count==1 assert) to /tmp and run it.

## 3. Versions (full detail in CHANGELOG.md)
- 0.1-0.8: base mixer, strips/buses, themes (purple + Potato-style theme/layout), scenes, MIDI mapping, hotkeys, recorder, FX hosting.
- 0.9.0-0.9.3: media player (queue, per-file volume, loop, MIDI file playback via fluidsynth), media player DSP panel (engine slot 12), MIDI mapping.
- 0.10.0: OBAN network input strip + stock VBAN fleshed out (see section 5).
- 0.10.1: OBAN lossless codec named VFLAC / VERTIFLAC; logo `assets/vflac.svg`.
- 0.10.2: clean exit. Everything VM starts (engine, pw-cli device holder, pw-cat, ffmpeg, recorder, effect hosts) dies with VM via `setpriv --pdeathsig`; startup `pwutil.sweep_stale(sock)` removes leftovers of a crashed run; excepthook stops PyQt6 aborting on stray exceptions; every closeEvent step runs.
- 0.10.3: tone knobs retuned (TREB high shelf 3.5 kHz, was 8 kHz; BASS low shelf 200 Hz, was 120 Hz; MID peak 1 kHz); media player DSP got a `[ PEQ ]` button (same 6-cell parametric EQ, slot 12, saved in config as `mp.dsp.eq`).
- 0.10.4: FX follow routing. Reverb and delay are per bus; a bus only runs them while a routed strip feeds it. `tests/test_fxroute.py`.
- 0.10.5: `[ NET ]` button on B buses, Protocol dropdown (VBAN / OBAN), IP to IP only (no broadcast, no open subscribe). `tests/test_netout.py`.
- 0.12.1: autosave of the whole config (2 s, atomic), net in/out restored at launch, start at login / minimized.
- 0.13.0: multichannel VBAN / OBAN (see section 5b).
- 0.11.0: 8 strips per pane (16 input strips, 16 buses), media slot 16, routing buttons in two columns (A left, B right), config migration, `Bus[n]` remote order A1-A5,B1-B3,A6-A8,B4-B8. `tests/test_cap16.py`. Heavy FX on all 16 strips at once can spike the callback (see CHANGELOG).
- 0.11.1: audio CD playback in the media player (`cdaudio.py`, `tests/test_cd.py`). cdparanoia fast mode `-Z` by default; titles via MusicBrainz if discid is installed.

## 4. Architecture facts
- Engine: JACK client on pipewire-jack, line-JSON over a unix socket. 24 input slots (0..15 strips, slot 16 = media player output via `config.MEDIA_SLOT`, its DSP panel is `mediadsp.py` built on `StripBase`), 16 buses (A1-A8 = 0..7, B1-B8 = 8..15, `config.BUS_IDS`), 8 net receivers (each with 8 feeds = rings), 4 net senders (each with 4 stereo rings and an optional channel matrix). Atomics + lock-free SPSC `Ring` (65536 frames). `panic = "abort"`: every parser must be bounds-checked.
- EQ: 9 cells per strip/bus: 0-5 parametric, 6-8 tone knobs (`mapping.TONE_CELL`, `mapping.tone_cmd`). Kinds: 0 peak, 1 low shelf, 2 high shelf, 3 LP, 4 HP, 5 notch, 6 BP.
- Virtual devices: `pwutil.NodeHolder` runs one long-lived `pw-cli`; nodes it creates (`VertiMeeter_*`, null-audio-sink) die with it. `linker.py` keeps links in sync in its own thread.
- Control commands (ctl.rs): strip, bus, eq, comp, gate, meters, vban_rx/oban_rx, vban_tx/oban_tx, etc. Meters include `vban` (rx stats), `vbtx` (tx stats), `text` (TEXT lines).
- Isolation for tests/screenshots (never touch his real graph or speakers): env `VERTIMEETER_SOCK`, `VERTIMEETER_CLIENT` (e.g. VMTEST), `VERTIMEETER_CONFIG_DIR`, `VERTIMEETER_NOLINK=1`. Run GUI tests with `QT_QPA_PLATFORM=offscreen`. Register `atexit eng.stop` in any test that starts an engine.
- Single instance via flock on `~/.config/vertimeeter/lock`. Desktop entry + icon are written on first start (`icon.install_desktop_entry`, since 0.7.3).

## 5b. Multichannel (0.13.0)
- Engine rx: `Params.net_rx[receiver * 8 + feed]` rings, `net_rx_slot` (strip slot or -1), `net_rx_feed` (left | right << 8 source channel). The receiver thread decodes up to 8 PCM channels (`pcm_decode_n`), resamples channel pairs (one `Resampler` each) and builds each bound feed from two channels. Priming / drift slip are decided on the first bound feed and applied to all. `net_rx_ch[k]` = channels seen (meters `vbch`).
- Engine tx: `net_tx_nch[k]` (0 = legacy single-bus tap), `net_tx_mask[k][ch]` (bus bitmask, bit = bus index), `net_tx_src[k][ch]` (0 pair position, 1 L, 2 R, 3 average). The mixer sums the ticked buses per channel after the bus loop (before MON) and pushes pairs to `net_tx[k * 4 + pair]`; the sender thread pops, resamples and interleaves (`pcm_pack_n`, counts clipped samples). >2 channels without `mx` is refused.
- Control: `oban_rx` with `slots` / `feeds`; `rx_map {k, slots, feeds}`; `oban_tx` with `ch`, `mx`, `src`; `tx_map {k, mx, src}`.
- GUI: strip kind `netch` (`NetChStrip`, cfg `src = {parent, chs}`), `config.rows_of / net_feeds / out_matrix`, `ChannelsDialog`, `ChannelMapDialog`, bus cfg `vban.map = {split, rows{key: {buses, src}}}` with row keys like `0-1` or `4`.

## 5. OBAN / VBAN (0.10.0)
- OBAN = "Open VBAN-compatible networking", own superset of VBAN, not affiliated with VB-Audio. Magic "OBAN", 28-byte VBAN-shaped header. PCM packets identical to VBAN except magic. Default port 6990 (VBAN stays 6980). Strip tooltip: "Open VBAN-compatible networking".
- Codecs: VBAN PCM, OBAN PCM (dt 0-5, SR table 6 kHz-705.6 kHz), Opus music/voice (system libopus via FFI), VFLAC lossless (own FLAC-style codec, fixed predictors order 0-3 + Rice, NOT libFLAC, byte-exact, 0x60), mu-law. Own codec IDs only under OBAN magic.
- VB-Audio closed codecs 0x10/0x20/0xF0: recognized, refused with a status message, never sent.
- Receiver: sender lock/IP allow-list, stream-name filter, reorder buffer, FEC/PLC concealment, resampler, jitter buffer, drift slip, status line. Never replies to audio packets. Optional (off by default): PING reply, TEXT commands (`Strip[n].Gain|Mute|Solo|Mono=`, `Bus[n].Gain|Mute|Mono=`).
- Dropped on purpose: reply-to-sender / auto-fallback (VBAN has no feedback channel, reflection risk). Replacement: status line + `[ Copy 'use PCM' note ]` button.
- Not implemented: VBAN-MIDI/serial, FRAME, chat, RT packets, VBCA/VBCV, encryption/auth (use Tailscale). Real Voicemeeter interop was never tested from the sandbox; he can test with VBAN PCM.
- No host/IP is ever hard-coded; addresses come from config/user input.

## 6. Decisions the user made (all in this chat)
- Codec name: VFLAC / VERTIFLAC (graphic = two-column stack V E R T I | F L A C). Internal codec key stays `lossless` on purpose (config/wire compatibility); display names say VFLAC.
- Tone corners: he approved retuning. Reason: a MIDI render has ~0.5% of its energy above 8 kHz, so treble -10 at 8 kHz moved it by 0.06 dB.
- Wants the Potato-style theme/layout shipped as a SEPARATE package in the same download (see section 7, not built yet). He agrees the Potato look is "close, not 1:1".
- Legal stance for the suite: VBAN spec is public, so VBAN is fine. Everything else of VB-Audio is closed: build from public behavior and own design only; no decompiling, no their code/art/names/trademarks, no private formats, check the Voicemeeter Remote SDK license before touching it. (Not legal advice.)
- Wants the app clean on exit (done in 0.10.2).

## 7. Open / pending
- [ ] Potato-style theme/layout split into its own package (`VertiMeeter-PotatoStyle/` next to the core folder, plugin folder scanned at start, menu entries only when installed, named "Potato-style (homage)", README note: unaffiliated, sampled colors only). Plan was given; he has NOT said "build it" for the exact plan yet. I never looked at how tangled the Potato code is in core.
- [ ] Optional: `[ Tone corners ]` settings so he can set the 3 shelf/peak frequencies himself.
- [ ] Optional: DSP params (EQ/reverb/3D) as MIDI-mapping targets (today only strip/bus gain, mute, solo, mono, media volume/mute, scene recall, macro).
- [ ] Optional: polish the KDE desktop entry (name "VertiMeeter '98", keywords, hicolor icon sizes, install/uninstall option). Already works for Favorites / Pin to Task Manager.
- [ ] Cosmetic: `LIM off` button is clipped in the media player DSP panel (pre-existing).
- [ ] Not built from earlier plans: surround pad (needs multichannel buses), MIDI feedback/lights, macro triggers other than key/MIDI, in-engine plugin hosting.
- [ ] Not verified by ear: treble/PEQ on a MIDI through the real engine. Ask him how it sounds.

## 8. Test suites (all isolated ones passed at 0.11.0; test_fx.py / test_dsp.py need VertiMeeter closed and test_fx.py still needs updating for per-bus FX)
- `tests/test_oban.py` (58 checks, ~130 s, needs engine built), `test_mediadsp.py`, `test_queue.py`, `test_midi.py`, `test_v06.py`, `test_v09.py`, `test_inputs.py`, `test_scenes.py`, `test_gui06.py`, `test_layout.py`; `cargo test --release` in `engine/` (13 unit tests).
- Run example: `cd <project> && QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_mediadsp.py`

## 9. Release / delivery routine
1. Bump `VERSION` in `src/vertimeeter/config.py` (and `engine/Cargo.toml` only if the engine changed), add a CHANGELOG.md entry at the top.
2. Run the relevant tests.
3. Zip (top folder `VertiMeeter/`, exclude dist, .venv, engine/target, _shots, _old, __pycache__):
```bash
cd "$HOME/Documents/scripts...malware...linux/_CLAUDE_/_FOR_VERTICAL_" && zip -qr VertiMeeter/dist/VertiMeeter_<x.y.z>.zip VertiMeeter -x "VertiMeeter/dist/*" "VertiMeeter/.venv/*" "VertiMeeter/engine/target/*" "VertiMeeter/_shots/*" "VertiMeeter/_old/*" "*/__pycache__/*" "*.pyc"
```
4. Deliver: `device_stage_files` the zip, then `SendUserFile` the staged path. (`device_commit_files` only accepts sources under `/mnt/user-data/outputs/`.)

## 10. Mistakes made in this chat (do not repeat)
- Ran `pwutil.sweep_stale()` by hand while his VM was open: it killed the running instance's engine. Never run it unless no VM is running (the app calls it only after the single-instance lock).
- `pkill -f <pattern>` killed my own shell once. Use `awk '/[v]ertimeeter-engine \/tmp\/vm/'` style loops.
- Old tests leaked engines and held pipes (fixed with atexit). Always check `ps -eo pid,ppid,etimes,args | grep -E "[p]w-cli|[v]ertimeeter-engine|[p]w-cat"` after test runs.
- Docs once overclaimed (Bus[n].Solo does not exist). Re-check docs against code.
- Said the Potato theme was my own drawing in a way that read as 1:1; it is an approximation. Be exact about "close" vs "identical".
