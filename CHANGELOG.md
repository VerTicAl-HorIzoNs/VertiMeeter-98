# Changelog

## 0.13.0 - multichannel VBAN / OBAN (up to 8 channels), channel maps
- Receive: a network input now understands PCM streams of 1 to 8 channels (VBAN PCM and OBAN PCM). Before, anything above 2 channels was read as the first two channels. The channel count is read from every packet header, shown in the strip box ("8ch") and remembered in the config (`net.ch`), so the channel list is filled in before the sender connects. Opus, VFLAC and mu-law stay 1-2 channels.
- Split a stream into strips: the network input settings have a `[ Channels... ]` button. It lists the stream's channels as pairs (CH 1-2, CH 3-4 ...; an odd last channel is single). The first row is the strip itself. Tick any other row and it becomes its own strip in the Virtual Inputs pane (kind `netch`, name "<input> CH 3-4") with fader, EQ, FX and A / B routing like any strip. `[ split ]` turns a pair into two mono strips (a mono channel plays on both sides). Unticking removes the strip; removing the input removes its channel strips. Up to 7 channel strips per stream; they count toward the pane's 8 strips.
- Send: the network out dialog has Channels = Mono, Stereo, 3 ... 8 (PCM only; choosing Opus / VFLAC / mu-law puts it back to Stereo) and a `[ Channel map... ]` button for 3 or more channels. The map is a checkbox grid, one row per channel or pair, one column per existing bus. Ticked buses are summed into that channel (A and B buses mixed freely). A stereo row sends bus left / right on its two channels; a mono row has `[ L / R / L+R v ]`. An empty row sends silence. Rows follow the channel count (pairs, odd last single) and can be split into mono rows. Mono and Stereo behave as before (this bus alone).
- Summed channels can pass full scale. Nothing is limited for you: the sender status shows "summed channels clipped N samples".
- Engine: a receiver owns up to 8 feeds (one ring and one strip slot each); a feed takes any two channels of the stream (the same channel twice = mono). A sender with a channel matrix has per-channel bus bitmasks and a source mode and pushes up to 4 stereo rings, the sender thread resamples and interleaves them. New control commands `rx_map` (rebind feeds without restarting the receiver) and `tx_map` (change the matrix live); `oban_rx` takes `slots` and `feeds`, `oban_tx` takes `ch` up to 8 with `mx` and `src`. `ch` above 2 without `mx` is refused. Meters carry `vbch` (channels seen per receiver). Old single-slot commands still work.
- Autosave (0.12.1) covers all of it: channel strips, splits, maps and the remembered channel count survive a crash or reboot, and the receivers / senders restart with them.
- Tests: `tests/test_multich.py` (engine: 8-channel in, splits, L/R pick, 6-channel matrix out, live edits, rebinding, 4-channel stream), `tests/test_v0130.py` (GUI: dialogs, child strips, matrix out, restart), 2 new Rust unit tests. `tests/shot_v0130.py` makes the screenshots.
- NOT verified: nothing was listened to by ear; no real multichannel VBAN sender or receiver (Voicemeeter, other apps) was tried, only VertiMeeter's own packets on loopback; more than 2 channels over a real network (UDP size is fine, 8 ch 16-bit 48 kHz is about 6 Mbit/s) was not tried. The Windows build was not recompiled.

## 0.12.1
- Autosave: the whole config (strips, buses, V/OBAN inputs and outputs, ports, stream names) is written within 2 s of any change, via temp file + fsync + atomic rename. A crash, power loss or kill no longer loses live settings (before, config was only saved on a clean exit).
- If config.json is unreadable, it is copied to config.json.unreadable.bak before defaults are used, so autosave cannot overwrite it.
- V/OBAN inputs left on start listening at launch; V/OBAN outputs left on start broadcasting at launch.
- Start at login: Engine > "Start VertiMeeter at login" (XDG autostart entry, 5 s delay) and "...start minimized" (`--minimized`). Disabled on Windows.
- Tests: tests/test_v0121.py (isolated): autosave logic, broken-config backup, autostart entry, rx/tx restored after a crash-style restart, restored receiver accepts audio.
- NOT verified: an actual desktop login (autostart entry written and checked, not triggered by KDE).

## 0.12.0 - Windows variant (experimental)
- New: runs on Windows 10/11. Same GUI, same mixer engine, DSP and routing; audio goes through WASAPI (cpal) instead of JACK/PipeWire. Linux behaviour is unchanged (JACK engine is still the default build; checked with a tone-in / bus-out run, 0 xruns).
- Engine: new `soft-backend` (software ports, mixer on its own thread, 48 kHz / 5 ms blocks) and `cpal-backend` (real devices). Adaptive resampling rings absorb clock drift between devices; the clock device paces the mixer, a timer paces it if no output is open. Live-adjustable playback buffer (`latency`, 10-500 ms, default 40; Engine > System settings on Windows).
- Windows control channel is loopback TCP with a per-run token (unix socket stays on Linux). Media files play through an engine feed (ffmpeg -> TCP -> engine) with closed-loop pacing; recorder writes 32-bit float WAV in the engine.
- Opus is loaded at runtime from opus.dll on Windows (no link-time dependency).
- GUI: hardware strips and A buses pick WASAPI devices (`@default` follows the system default); virtual strips and B buses pick a capture / loopback device or any playback device (e.g. a third-party virtual cable). Global hotkeys (low-level hook) and MIDI (winmm) work on Windows. Child processes die with the app (job object).
- Windows limits: no virtual audio devices (needs a driver, so use a third-party cable), no per-app routing, no JACK patch bay / EXT-RET FX loops, no CD playback, shared-mode WASAPI only (latency about 40 ms+ per side), linear resampling. ffmpeg.exe must be supplied (fluidsynth.exe optional).
- Packaging: install_windows.bat, build_engine_windows.bat, vertimeeter.bat, GitHub Actions workflow for the engine exe. See README_WINDOWS.md.
- Tests: tests/test_winengine.py (engine over TCP: auth, feed, recorder, tone) and tests/test_windows.py (GUI in fake-Windows mode, VERTIMEETER_FAKE_WIN=1) pass on Linux.
- NOT verified: no run on real Windows. All cfg(windows) code (WASAPI/loopback devices, hotkey hook, winmm MIDI, job object, opus.dll loading, MMCSS) has never been compiled or run, and nothing was listened to by ear. Expect a compile fix or two on first build.

## 0.11.2
- Fix: the media player DSP panel did nothing after the 0.11.0 upgrade. Its saved settings still said engine slot 12, so every EQ / limiter / tone control went to the wrong engine strip while the audio runs through slot 16. The slot is now forced to 16 whenever the panel is built (old configs and old scenes included). Found in your live config (`mp.dsp.slot` was 12). Anything saved from the DSP panel before this fix is unaffected, only the slot number is corrected.
- tests/test_mediadsp.py now checks that a pre-0.11 `dsp` block gets fixed. The earlier run missed it because that test starts from a fresh config.

## 0.11.1
- Audio CD playback in the media player: `[ + FILES v ] > Add audio CD tracks` reads the disc TOC (cdparanoia) and adds one row per track, each with its own seek, loop, volume and routing, through the one media sink (fader, DSP, routes). `Eject CD` is in the same menu.
- Playback is cdparanoia | ffmpeg | pw-cat. Default is fast mode (`-Z`, no error correction) because on the dev drive full paranoia ran at 0.8x real time and stuttered (fast: 2.6x, `-Y`: 1.2x). Set `"cd_safe": true` in config.json for full error correction. A scratched disc can click in fast mode.
- The first audio arrives about 3 to 8 s after pressing play while the drive spins up (the position clock waits an estimated 3 s). Seeking uses a cdparanoia sector offset, so it does not read the whole track.
- Track names from MusicBrainz in the background when `libdiscid` and the `discid` python package are installed (taken from your Audio Death Toolkit code), otherwise "CD track NN". Never blocks playback.
- A data disc (files on it) is mounted with udisksctl and the file picker opens on it. Device is `cd_device` in config.json (default /dev/sr0). CD rows are not restored after a restart.
- New `src/vertimeeter/cdaudio.py`, `tests/test_cd.py` (fake drive: TOC, spans, real ffmpeg bytes, menu, rows; isolated). install.sh and README list cdparanoia.
- Checked on a real disc: TOC read, and real audio through cdparanoia | ffmpeg at the start of a track and when seeking to 60 s and 120 s. Not checked: the full path into the media sink by ear (needs VertiMeeter closed for test_media).

## 0.11.0
- Every pane now holds up to 8 strips: Hardware Inputs 8, Virtual Inputs 8 (virtual, VBAN and OBAN input strips share these 8), Hardware Out 8 (A1-A8), Virtual Out 8 (B1-B8). That is 16 input strips and 16 buses. The media player is separate and does not count.
- Engine: 16 buses (A1-A8 = 0..7, B1-B8 = 8..15; B1 used to be index 5), 16 strip slots, media player moved from slot 12 to slot 16. Insert loops and voice modeler now cover all 16 strips plus the media player. Engine ports: S1-S16 strips, S17 media, A1-A8, B1-B8. Network senders stay at 4 and receivers at 8.
- Each strip's routing buttons are now two columns: A1-A8 on the left, B1-B8 on the right. Strips are 158 px wide (were 136).
- Old config files are migrated on load: the media player slot goes 12 -> 16 and every strip gets send entries for the new buses, off by default. Scenes, macros and MIDI maps refer to buses by name, so they keep working.
- Full panes say so in the status bar ("Virtual Inputs is full (8 strips max)") instead of the old "All 12 strip slots are in use".
- VBAN-TEXT `Bus[n]`: 0-7 are still A1-A5, B1-B3 (same as Voicemeeter Potato), then 8-10 = A6-A8 and 11-15 = B4-B8.
- Tests: new tests/test_cap16.py (caps, config migration, all 16 strips into all 16 buses) and tests/shot_cap16.py (full-layout screenshot). Existing tests moved to the new bus indices (B1 = 8) and media slot (16). mockup/VertiMeeter_mockup-7.html shows the two-column routing and the 8-strip caps.
- Load: 16 strips into 16 buses with plain mixing was about 2 ms worst callback in a 21 ms window with no xruns. With reverb, delay and denoiser maxed on every strip at once, worst callbacks of 4 to 16 ms and occasional xruns were seen on the dev machine (denoiser x16 is the biggest part). Idle buses and strips are skipped. A bigger buffer fixes it if you ever run that.
- Not verified: ears, and a real second PC.

## 0.10.5
- Virtual outs (B1-B3) get a `[ NET ]` button under the bus FX button. It lights while that bus is sending. Hover shows `<host>:<port>`, protocol, codec and the live sender status. Click opens the Network out dialog. A1-A5 keep the right-click > Network out entry.
- Network out dialog: new Protocol dropdown, `VBAN` (PCM only, works with Voicemeeter / VBAN receivers, port 6980) or `OBAN` (PCM, Opus music / voice, VFLAC, mu-law, port 6990). Switching protocol swaps the codec list and the default port. Existing saved settings load as before (VBAN PCM = VBAN, any other codec = OBAN).
- The dialog says it is IP to IP: one target host per bus, nothing is sent until you type a host and turn SEND on. A status line shows what is being sent and the rough kbps. OK is refused with "address and port required" if SEND is on and the host is empty (before, SEND was silently turned off).
- No host or IP is stored in code. Broadcast and open-subscribe modes were dropped on purpose (UDP has no connections, and broadcast does not cross separate networks).
- GUI only: the engine is unchanged since 0.10.4.
- New test tests/test_netout.py (isolated, loopback receiver owned by the test): button exists on B1 only, protocol / codec / port logic, empty host stays open, VBAN then OBAN packets arrive with the right magic and stream name, the button lights, the stream stops on OFF. tests/shot_netout.py makes the screenshots.
- Not verified: sending to a real second PC, and a real Voicemeeter receiving the VBAN stream.

## 0.10.4
- Fix: a strip's RVB / DLY sends leaked into every bus, vMics (B1-B3) included, even when the strip was not routed there. Reverb and delay now run per bus, and each one is fed only by the strips routed to that bus (A1..A5 / B1..B3 buttons). No route, no effect.
- The same rule applies to the EXT RET1 / RET2 returns: they only reach buses that have a routed strip with an EXT1 / EXT2 send. Limit: the external FX hears every strip's EXT send (they go out as one mix), so a bus that has some routed EXT sender also hears the others' wet signal.
- Effect send level is the strip's send gain times its RVB / DLY knob, so a lowered bus send also lowers the effect on that bus.
- Behavior change: an RVB / DLY send with no bus routed is silent now. Before, un-routing the dry path left a wet-only sound. Reverb tails that are already ringing finish decaying after you un-route.
- Idle buses cost nothing: a bus with R.RET / D.RET at 0 and nothing routed skips its reverb / delay and wipes the old tail, so it never replays later.
- New test tests/test_fxroute.py (isolated engine): A2 and B1 stay at -180 dB while A1 has the effects, B1 hears it only once routed. test_regress.py updated to keep the strip routed for the reverb / delay checks.
- The denoiser check in test_regress.py (noise_dns vs noise_off) already failed on 0.10.3 and still does, same numbers, so it is not caused by this change. Not fixed here.

## 0.10.3
- Tone knobs now act where music has energy: TREB is a high shelf at 3.5 kHz (was 8 kHz, which moved a MIDI render by 0.06 dB at -10) and BASS a low shelf at 200 Hz (was 120 Hz). MID stays at 1 kHz. This changes BASS / MID / TREB on every strip and bus, so saved mixes sound a bit different.
- Media player DSP panel: new PEQ button, the same 6-cell parametric EQ as the other strips, applied to everything the player plays (MIDI renders included). Saved in the config; the button shows * when a cell is on.

## 0.10.2
- Fix: virtual devices (VertiMeeter_*), the engine and the media player process were left running after VertiMeeter crashed or was killed, so Pavucontrol still listed them.
- Everything VertiMeeter starts (engine, pw-cli device holder, media player, recorder, effect hosts) now dies with it, even on a crash or kill -9 (kernel parent-death signal via setpriv).
- On start, leftovers from an earlier crash are swept (old engine on the same socket, orphaned device holder, stale VertiMeeter_* devices). A running VertiMeeter is never touched.
- A stray exception in the GUI is logged instead of aborting the whole app (PyQt6 aborts by default, which skipped all cleanup). Also handles SIGHUP.
- Closing: every shutdown step now runs even if an earlier one fails.

## 0.10.1
- The OBAN lossless codec is now named VFLAC (VERTIFLAC): V-E-R-T-I + F-L-A-C. Shown as "OBAN VFLAC (lossless)" in the codec list, README and packet doc.
- Wire format unchanged (codec 0x60), so 0.10.0 senders and receivers still talk to this version.
- Added assets/vflac.svg (logo stack).

## 0.10.0
- New strip type: Network Input (OBAN) (Virtual Inputs `[ + v ]`, tooltip "Open VBAN-compatible networking"). OBAN uses the VBAN header layout with the magic "OBAN" and default port 6990. One UDP socket reads VBAN PCM and OBAN streams on the same port. Nothing about the classic VBAN strip changes: it still listens on 6980 and ignores OBAN packets.
- Codecs (sender and receiver): VBAN PCM (Voicemeeter compatible), OBAN PCM (same bytes, other magic), OBAN Opus music, OBAN Opus voice (in-band loss protection), OBAN lossless (bit-exact, own FLAC-style frames, about 40% smaller than PCM on music), OBAN mu-law (8-bit voice, very small). PCM formats: 8 / 16 / 24 / 32-bit, 32 / 64-bit float, mono or stereo, any VBAN sample rate (6 kHz to 705.6 kHz in, resampled with a windowed-sinc resampler).
- Receiver: reorder buffer by frame counter, duplicate and late packet drop, Opus loss recovery (FEC, then concealment), silence fill for PCM / lossless / mu-law gaps, adjustable jitter buffer (0-500 ms), tiny single-frame slips for clock drift between machines, sender lock (first sender wins until it is silent for 3 s) or an IP filter, stream name filter, Accept mode (auto / PCM only / coded only).
- Status line on every network strip: sender IP, codec, rate, kbps, loss %, late packets, jitter, buffer. A sender using a closed VB-Audio codec (VBCA, VBCV, USER) is named and refused with a clear message ("ask them to switch the sender to PCM"). Nothing is sent back to the sender. [ Copy 'use PCM' note ] puts a ready-made sentence on your clipboard.
- Bus menu: "Network out (OBAN / VBAN)..." with Host, Port, Stream, Codec, Sample rate, Channels, PCM format, Opus bitrate (6-510 kbps) and Opus frame (5 / 10 / 20 / 40 ms). Four senders and eight receivers at once. The sender status shows in the bus name tooltip.
- Fleshed-out VBAN: every PCM format, the receiver jitter buffer and statistics above, answers VBAN device scans (PING, off by default, never larger than the scan), and VBAN-TEXT remote control (off by default, needs a sender IP filter, stream name Command1): `Strip[n].Gain=-3;` `Bus[n].Mute=1;` strips also .Solo / .Mono. Lines starting with // or # are ignored.
- Not done: VBAN-MIDI / serial, VBAN-FRAME, VBAN chat, the RT packet service, VB-Audio's VBCA / VBCV codecs (closed bitstreams), and more than two channels (the first two are used).
- Engine: new codec.rs (resampler, mu-law, lossless, Opus through system libopus) and oban.rs (receiver / sender threads). Needs `sudo pacman -S opus` and an engine rebuild (`cargo build --release` in engine/, or ./install.sh). Nothing runs on the audio callback.
- New tests: tests/test_oban.py (about 60 checks through a real isolated engine), 13 cargo unit tests, tests/shot_oban.py (screenshots). test_mediadsp and test_queue now stop their engine when they finish.
- New doc: docs/OBAN_PACKET_FORMAT.md. OBAN is not affiliated with or endorsed by VB-Audio.

## 0.9.3
- Media player queue: the global play button plays the files top to bottom, and each file starts when the one before ends. Play pauses / resumes the queue. A file with its loop button on keeps repeating and holds the queue. Pressing a file's own play button makes the queue continue from that file.
- [ ↻ ALL ] repeats the whole queue. [ MIX ] switches back to the old behaviour (play starts every file at once).
- Tape-deck buttons: previous and next file side by side (previous restarts the current file if it is more than 3 s in), then stop and play. The skip 10 s button is gone, use the seek bar.
- Seek bar: a time bubble ("1:23 / 3:09") follows the handle while you drag, and the file jumps when you let go. Files that have been played keep a full bar. The row of the file the queue is on has a marker.
- Per-file up / down buttons reorder the queue.
- The DSP panel is its own movable window now, closed by default (DSP button). [ DOCK ] inside it snaps it to the right end of the player, [ FLOAT ] pops it out again. Window position and docked / floating state are saved.
- Rows are narrower so the list has more room when the panel is docked.
- New tests: tests/test_queue.py (fake players, plays no audio).

## 0.9.2
- Media player DSP panel (right side of the player, DSP button shows / hides it): the same processing a virtual input has, applied to everything the player plays: BASS / MID / TREB, pad (3D, pan, modulation), M.C., karaoke, RVB / DLY / PAN, EXT 1 / EXT 2 with POST, limiter, FX (voice modeler and insert FX host).
- Per-file volume stays on each file. Player volume, mute and routing stay in the header. The DSP is saved in your config and in scenes.
- Engine: voice modeler and an insert loop (INS13) for the media slot.
- New test: tests/test_mediadsp.py.

## 0.9.1
- Media player plays MIDI files (.mid .midi .kar). Each MIDI row has a MIDI button: pick the SoundFont and open Tracks / instruments, where every track (named as the sequencer exported it, e.g. onlinesequencer.net) can be switched on / off and given another General MIDI instrument. Drum tracks stay drums.
- MIDI is rendered to a WAV with fluidsynth when you press play (a few seconds, cached in ~/.cache/vertimeeter/midi), then plays like any other file with volume, seek, loop and the player's routing.
- Needs fluidsynth and a SoundFont: sudo pacman -S fluidsynth soundfont-fluid
- No extra Python packages (own MIDI reader / writer). New test: tests/test_midi.py (sample file in tests/data).

## 0.9.0
- Voice modeler (strip FX button): pitch shift -12..+12 semitones, ring modulator, mix, presets (Chipmunk, Deep, Giant, Robot, Alien). Pitch only, no formant correction, adds about 20 ms to that strip while on.
- Modulation pad mode (third pad mode on every input): x = tremolo rate 0.5-12 Hz, y = depth, bottom = off.
- Bus modes: 14 bus modes (6 added: Phase flip, Side (mono), LFE only, High-pass 120, Widen, Narrow).
- Insert FX host (strip FX button): run an external JACK / PipeWire effect on a strip. LV2 plugins are listed from your system and started with jalv (sudo pacman -S jalv), or give any command (Carla, a JACK effect). The engine sends the strip out to the effect and takes it back with a wet/dry mix. If the effect dies the insert switches itself off so the strip never goes silent.
- C5 multiband compressor on every output bus (the bus FX button, also "Switch to C5" in Special FX): 5 bands with crossovers 120 / 500 / 2000 / 6000 Hz, threshold / ratio / makeup per band, 4th-order Linkwitz-Riley crossovers.
- MIDI mapping (Tools > MIDI mapping): LEARN a note / CC and map it to strip / bus gain, mute, solo, mono, media volume / mute, scene recall or a macro. Reads ALSA raw MIDI (/dev/snd/midi*), no extra Python packages. Input only, no lights / motor faders.
- Global hotkeys (Tools > Global hotkeys): each macro can have a key combo (CAPTURE) that works while another window has focus, also on Wayland. Listens on /dev/input (needs the input group, python-evdev). Keys are never grabbed.
- Macros can also be started by a MIDI message (LEARN field in the macro editor).
- Streamer view (Tools > Streamer view): small window with scene buttons, your macro buttons and output mutes, optional always-on-top.
- Engine: `vmfxtest` helper binary (a JACK passthrough used to test the insert loop). New tests: tests/test_v09.py (DSP), tests/test_inputs.py.
- Not built: surround pad (needs multichannel buses), MIDI feedback, macro triggers from mouse / gamepad / audio level / VBAN-TEXT.

## 0.8.0
- Preset scenes (Tools > Preset scenes): 64 slots. Store the current mix, click to recall, right-click to store / rename / clear. F1-F12 recall scenes 1-12 while the mixer window is focused. A scene holds the mix only (gains, mutes, solos, pans, EQ / dynamics / FX knobs, routes, bus modes, media master), never devices, names or network settings. Saved in your config.
- Macro Buttons (Tools > Macro buttons): 80 buttons, each a list of actions run in order. Actions: recall a scene, strip / bus mute, solo, mono, gain, route a strip to a bus, media player (play/pause, stop, next, prev, mute, volume), run a program of your own.
- Per-app volume: right-click or scroll an app listed in a virtual input to change only that app's volume (presets, mute). Runs off the GUI thread.
- New test: tests/test_scenes.py. Not built yet: plugin hosting, voice modeler, MIDI, global hotkeys, streamer view, macro triggers (buttons are clicked in the window for now).

## 0.7.3
- Fixed: moving any fader or knob made every meter lag. The engine link sent each value one at a time and skipped meter updates while commands were queued. Commands are now batched and merged (newest value wins) and meters always refresh at 30 Hz.
- Title bar logo is back to VERTIMEETER '98. Only the program icon is the VM'98 mark.
- Taskbar icon: the app now writes its own user-local .desktop entry and icon on start so the desktop shows the VM'98 icon instead of a generic one.

## 0.7.2
- New logo: the title bar now reads VM'98, and the same VM'98 mark is the program icon (window, taskbar, app menu). Icon is painted in code; `assets/vm98.png` is the same image for the app-menu entry (install.sh copies it).

## 0.7.1
- Fixed: dragging a media file's volume slider froze the whole program. Per-file volume is now applied by a background thread (newest value wins), so the GUI never waits on pactl.

## 0.7.0
- UI rebuilt to match VertiMeeter_mockup-5.html: same palettes (Purple and Potato, taken from the mockup's CSS), strip and bus sizes, tapered fader scale, segmented meters, 28x20 route buttons, pad sizes, pane bars with scroll slider and `-` / `+ ▾`, gradient title bar, REVERB / DELAY / EXTERNAL FX RETURN boxes, status bar. Mockup copy is in `mockup/`.
- Media player now has ONE global output: `VertiMeeter_Media_Player_Output` (shown as "VertiMeeter Media Player Output"). Every file plays into it, so system sound settings show one device instead of one per file. It has one fader, meter, mute and A/B routes; each file keeps play, seek, loop and its own volume.
- All names are renameable: double-click a strip, bus, pane title or the media player title. Virtual cables and B mics change their name in PipeWire too (the node is recreated and playing apps are moved back).
- Hover a virtual input name to see which apps are playing into that cable.
- Default names are "Hardware Input n", "Virtual Input n", "Network Input n" (old names migrate). Panes: Hardware Inputs, Virtual Inputs, Special FX, Hardware Out, Virtual Out.
- New `--add-media FILE...` flag for screenshots. Tests updated to the new widgets.
- Removed: per-file media sinks and per-file bus routing.

## 0.6.0
- UI now matches the mockup control set. Hardware strips: INTELLIPAN pad with a third COLOR mode (x = dark/bright, y = presence), PAN knob synced with the pad, EXT1/EXT2 sends, POST, LIM, FX slot button (disabled until plugin hosting exists), SIGNAL dB readout, MONO/SOLO/MUTE in one row. Virtual strips: M.C. + K1/K2 karaoke, `+ APP` menu (moves a running app into the strip) with the list of apps playing into it. Buses: DELAY, LIMIT, PEQ, VBAN, bus mode, SEL, MON, fader dB readout.
- Top bar with logo/version and live engine state (rate, buffer, xruns, DSP %). Menus: File, Engine (reset counters, system settings), Record, Tools (audio map), Theme, Help. Panes scroll vertically when the window is short.
- Engine: strip and bus peak limiters, post-fader EXT sends (JACK ports EXT1/EXT2, returns RET1/RET2 that join the reverb/delay return), karaoke and centre-only, 8 bus modes, per-bus delay up to 500 ms, MON (bus also on A1).
- VBAN network audio. Receive: Network strip (stream name, UDP port, optional sender IP filter), 16-bit/24-bit/32-bit/float/double PCM, resamples other rates, prebuffers to stay glitch-free. Send: VBAN button on every bus (host, port, stream, 16-bit or float). No address or host is stored or assumed anywhere; you type them.
- Recorder: Record menu records any bus to WAV. System settings dialog sets the PipeWire buffer size (Auto by default). Audio map / patch bay: links you add are saved and kept by the linker.
- Tests no longer touch a running VertiMeeter: new isolated engine tests (test_v06, test_regress, test_gui06), full-stack tests refuse to run while the app is open, screenshots and tests use their own engine socket, JACK client name and config directory (VERTIMEETER_SOCK / VERTIMEETER_CLIENT / VERTIMEETER_CONFIG_DIR / VERTIMEETER_NOLINK).
- Fixed: restarting a VBAN receiver quickly failed with "address in use" (old thread now joined before restart).

## 0.5.5
- Denoiser (RNNoise via nnnoiseless): DNS knob on hardware strips, 0-10 wet/dry amount. Fixed 10 ms latency, dry path time-aligned. Active at 48 kHz only (strip stays untouched at other rates). Tuned for voice: on stationary white noise it settles over a few seconds (-23 dB -> about -28 dB in the test, deeper on some material); worst callback about 0.6-1 ms. Turn it off for music or media.
- net.rs (VBAN ring/UDP code) added to the engine source tree, not wired in yet (0.6.0).

## 0.5.4
- Custom layout: every pane is a free rectangle. Theme > Custom layout > Edit layout shows drag/resize overlays (snap grid), with SAVE / SAVE AS / REVERT / DONE, named saved layouts you can load or delete, and per-pane show/hide. Starts from the Default arrangement. Switching layouts no longer risks destroying panes.
- 3D positioner (Intellipan): per-strip pad with PAN and 3D modes (right-click the pad to switch). 3D uses interaural time and level difference, head-shadow low-pass and a darker "behind" position. Verified: at hard right the far ear drops ~20 dB at 6 kHz, hard pan silences the far ear.
- B buses are real virtual microphones ("VertiMeeter B1 (virtual mic)") that Discord, OBS and others can pick as an input. Virtual strips are sinks apps can play into.
- Engine: audio buffers are pre-touched so the first callback never page-faults (worst callback dropped from 2.1 ms to 0.36 ms in testing).

## 0.5.3
- Special FX in the engine and GUI: stereo reverb (Freeverb topology) and 8-tap delay (per-tap time, level, pan, shared feedback). Per-strip RVB/DLY knobs send to them, per-bus R.RET/D.RET knobs return them. Verified on the live engine.
- Detail dialogs: 6-cell parametric EQ with live response curve (strip EQ button, bus PEQ button), compressor and gate with exact values (DYN button, or right-click COMP/GATE knob). Settings persist and are re-sent at start.

## 0.5.2
- Engine DSP: 9-cell RBJ biquad EQ per strip and per bus (6 parametric cells plus 3 tone cells), stereo-linked compressor and noise gate per strip. All parameters are lock-free atomics; coefficients refresh on the audio thread only when changed. Unit-checked on the live engine: +12 dB peak, 200 Hz low-pass, compressor and gate reduction, and bus EQ match their targets.
- GUI: COMP and GATE knobs on hardware strips, BASS/MID/TREB (+-12 dB) on virtual strips and buses now act on the audio.
- Idle strip slots cost nothing in the callback.

## 0.5.1
- Media player: add any number of files (up to 12 at once), any format via ffmpeg, video plays audio only. Per file: play/pause/stop, seek, loop, volume, and routing to any bus with a per-bus level (click the route button for precise control, hover it for a live tooltip). ffmpeg feeds pw-cat directly, so Python is not in the audio path.
- Engine: 24 input slots (12 strips + 12 media), per-send level, smoothed.
- Visible media sliders, dim pane scrollbar when nothing needs scrolling.

## 0.5.0 (rewrite)
- New native Rust mixing engine (`engine/`): JACK/pipewire-jack client, 12 stereo strip slots x 8 buses, per-strip send matrix, gain, pan, mono, mute, solo, smoothed (no zipper clicks), peak meters.
- Stutter fix for the 0.2.0 "IEMs pause ~2x/second with a hardware mic" bug. The old Python callback is gone. The new callback does no allocation, no locks, no syscalls and no logging, flushes denormals, and the engine no longer forces a low latency on the graph (it follows the system quantum, 1024 by default). Nothing polls inside the audio path. Link management runs in a separate slow thread. The status bar shows xruns and the worst callback time so any glitch is visible.
- PyQt6 GUI rewritten to match mockup v5: five panes (Hardware Inputs, Virtual Inputs, Special FX, Hardware Out A, Virtual Out B), per-pane scroll slider with `[ - ]` and `[ + v ]`, live device lists in the add menu, thick purple draggable splitters with remembered sizes, 16:9 lock, Theme menu with THEME (VertiMeeter '98, Voicemeeter Potato) and LAYOUT (Default, Potato) sections.
- Visible fader/slider tracks, fills and handles.
- No hosts or IPs are stored by default anywhere.
- Media player, DSP (EQ, comp, gate, FX), VBAN, MIDI, Custom layout editor, dialogs: still to come (see FEATURES.md).

## 0.2.0
- Python engine (superseded).

## 0.1.0
- First release.
