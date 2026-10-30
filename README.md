# VertiMeeter '98

A Voicemeeter Potato replacement for Linux (PipeWire), with a Win9x dark-purple look (or a Potato look-alike theme).

Version: 0.13.0 (multichannel VBAN / OBAN up to 8 channels; 0.12.1 autosave, start at login; 0.12.0 Windows variant added, experimental: see README_WINDOWS.md) (see CHANGELOG.md, FEATURES.md for the full parity plan)

## Architecture
- `engine/` native Rust mixing core, a JACK client (pipewire-jack). 24 stereo input slots (16 strips + the media player), 16 output buses (A1-A8, B1-B8), 2 external FX sends (EXT1/EXT2) and 2 returns (RET1/RET2). The audio callback never allocates, locks or logs. It follows the system quantum and does not force a latency. Controlled over a unix socket (line JSON).
- `src/vertimeeter/` PyQt6 GUI. Handles devices (pactl / pw-link / pw-cli), link management in its own slow thread, config, dialogs.
- Audio CDs: `[ + FILES v ] > Add audio CD tracks` lists the tracks on the disc in the drive (default `/dev/sr0`, key `cd_device` in config.json) as rows that route like any file. Plays as cdparanoia | ffmpeg | pw-cat, fast mode (no error correction) by default; set `"cd_safe": true` for full paranoia (slower than real time on some drives). Track names come from MusicBrainz if `libdiscid` and `pip install discid` are present, else "CD track NN". Data discs are mounted with udisksctl and open the file picker. `Eject CD` is in the same menu. CD rows are not restored after a restart. Needs cdparanoia.
- Media files: ffmpeg decodes any format (video = audio only) straight into pw-cat, which plays into the single `VertiMeeter_Media_Player_Output` sink feeding one engine slot.
- VBAN / OBAN network audio: UDP receive/send threads in the engine feed lock-free rings. Nothing hard-codes a host or IP. Hosts, ports and stream names come only from what you type (saved in your config).

## Install (Arch)
```
sudo pacman -S --needed pipewire pipewire-jack pipewire-pulse wireplumber python rust ffmpeg cdparanoia
./install.sh      # builds the engine, creates .venv, adds an app-menu entry
./vertimeeter.sh
```
Config: `~/.config/vertimeeter/config.json` (single instance lock next to it).

## Using it
- Panes: Hardware Inputs, Virtual Inputs, Special FX, Hardware Out (A), Virtual Out (B), Media Player. Each pane has its own scroll slider and `[ - ]` `[ + v ]` (the list reads devices live when opened). Panes scroll vertically when the window is short.
- Drag the thick purple borders to resize. Sizes are remembered.
- Theme menu: THEME (VertiMeeter '98 / Voicemeeter Potato), LAYOUT (Default / Potato / Custom), Lock 16:9.
- Hardware strip: INTELLIPAN pad (PAN / 3D / COLOR, right-click to switch), EQ, COMP / GATE / DNS (denoiser), PAN / RVB / DLY, EXT1 / EXT2 sends with POST (post-fader), LIM (strip limiter, right-click for ceiling), A/B routing.
- Virtual strip: BASS / MID / TREB, pad, M.C. (centre only) and K1/K2 karaoke, `+ APP` (move a running app's stream into the strip; apps already playing into it are listed).
- Network strips (`+ v` in Virtual Inputs): Network (VBAN) strip and Network Input (OBAN) strip. Stream name, UDP port, optional sender IP filter, jitter buffer, LISTEN. See "Network audio" below.
- Bus: tone knobs, R.RET / D.RET effect returns, DELAY (ms), LIMIT, PEQ master EQ, mode (Normal, Mono, Swap L/R, Invert L/R, Left/Right only, Side only), SEL, MON (also hear this bus on A1), Network out (OBAN / VBAN) in the bus name's right-click menu. Virtual outs (B1-B3) also have a `[ NET ]` button for it: pick VBAN or OBAN, type the receiver's host, press SEND. It is IP to IP, one target per bus, and the button lights while it sends.
- Special FX: reverb, 8-tap delay, external FX return (RET1/RET2), JACK PORTS (audio map) to wire EXT/RET to Carla or any other host.
- FX follow the routing: a strip's RVB / DLY / EXT sends only reach the buses (A1..A8, B1..B8) that strip is routed to. Each bus has its own reverb and delay.
- Media: `+ FILES`, one OUTPUT route button / fader / meter / mute for the whole player; per-file play, seek, loop, volume. Double-click any name to rename it.
- Engine menu: reset counters, system settings (PipeWire buffer size). Record menu: record any bus to WAV. Tools: audio map / patch bay (links you add are remembered).
- The top bar shows engine state, rate, buffer, xruns and DSP load (worst callback vs budget). If you hear a glitch, check there first.

## Tests
Safe while VertiMeeter is running (own engine, own socket, own port names, no links to your graph):
```
.venv/bin/python tests/test_v06.py       # limiters, karaoke, bus modes, MON, VBAN loopback, EXT/RET ports
.venv/bin/python tests/test_oban.py      # VBAN + OBAN: every codec, loss, reorder, floods, filters (about 2 min)
.venv/bin/python tests/test_regress.py   # EQ, comp, gate, pan, 3D, reverb, delay, denoiser
.venv/bin/python tests/test_fxroute.py   # FX only reach the buses a strip is routed to
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_cd.py   # audio CD support with a fake drive (TOC, seek span, cdparanoia | ffmpeg bytes)
.venv/bin/python tests/test_cap16.py   # 8 strips per pane, config migration, 16 strips into 16 buses (set CAP_LIGHT=1 for plain mixing only)
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_netout.py  # NET button + Network out dialog, VBAN / OBAN packets on loopback
.venv/bin/python tests/test_multich.py   # 0.13: 8-channel in, splits, 6-channel matrix out (engine only)
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_v0121.py  # 0.12.1: autosave, restart restore, start at login
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_v0130.py  # 0.13: channel dialogs, channel strips, matrix out, restart
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/selftest.py     # engine + xrun check
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_layout.py  # layouts
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_gui06.py   # GUI controls drive the engine
```
These build real virtual sinks, so they refuse to run while VertiMeeter is open (close it first):
```
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_media.py   # file -> ffmpeg -> engine -> bus
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_dsp.py     # EQ / comp / gate numbers
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_fx.py      # reverb / delay
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_3d.py      # 3D positioner
QT_QPA_PLATFORM=offscreen .venv/bin/python tests/test_dns.py     # RNNoise denoiser
```
Screenshots (isolated, never touches a running instance): `./vertimeeter.sh --screenshot out.png --reset`

## Not done yet
Surround pad (needs multichannel buses), MIDI feedback (lights), macro triggers other than key / MIDI, plugin hosting inside the engine (plugins run out of process through the insert host). See FEATURES.md.

## Scenes and macros (0.8.0)
- Tools > Preset scenes: 64 saved mixes, F1-F12 recall.
- Tools > Macro buttons: 80 buttons with mixer / media / scene / program actions.
- Right-click or scroll an app inside a virtual input for its own volume.

## 0.9.0 additions
- Strip FX button: voice modeler (pitch + ring) and insert FX host. LV2 needs `sudo pacman -S jalv`; any JACK effect works with a custom command.
- Third pad mode MODULATION (tremolo). 14 bus modes. C5 multiband compressor per output bus.
- Tools > MIDI mapping: LEARN controls. Needs read access to /dev/snd/midi* (audio group).
- Tools > Global hotkeys: macro key combos while another window has focus. Needs the `input` group:
```
sudo usermod -aG input $USER
```
  then log out and in. Needs `pip install evdev` (in requirements.txt).
- Tools > Streamer view: small always-on-top-capable control window.
- Tests: `tests/test_v09.py` (DSP, own engine) and `tests/test_inputs.py` (offscreen, isolated).

## MIDI in the media player (0.9.1)
- Add .mid files like any other file. Press MIDI on the row for the SoundFont and per-track on / off + instrument.
- Needs fluidsynth and a SoundFont:
```
sudo pacman -S fluidsynth soundfont-fluid
```
- Renders on play (cached). Sound quality depends on the SoundFont.

## Media player DSP (0.9.2)
- DSP button in the player header shows the panel: EQ, pad, M.C., karaoke, reverb / delay / pan, EXT sends, limiter, FX for the whole player output.
- Each file keeps its own volume.

## Media queue (0.9.3)
- Play = top to bottom queue. [ ↻ ALL ] repeats it, [ MIX ] plays everything at once. Previous / next file next to each other like a tape deck.
- Seek bar shows the time while you drag. Played files keep a full bar. Up / down buttons per file reorder.
- DSP button opens the player DSP as a movable window, [ DOCK ] snaps it to the right end of the player.

## Network audio: VBAN and OBAN (0.10.0)
**Multichannel (0.13.0).** PCM streams can carry 1 to 8 channels. Receive: click the network input's box, press `[ Channels... ]`, and tick the pairs or single channels you want as their own strips (each gets a fader, EQ and A / B routing; `[ split ]` makes a pair two mono strips). Send: in the network out dialog set Channels to 3 or more (PCM only) and press `[ Channel map... ]`: one row per channel or pair, one checkbox per bus, ticked buses are summed into that channel, mono rows pick L, R or L+R. Opus, VFLAC and mu-law stay 1-2 channels.
VertiMeeter is not affiliated with or endorsed by VB-Audio. VBAN is VB-Audio's protocol; its base (PCM audio, TEXT) is published and free "AS IS". OBAN ("Open VBAN-compatible networking") is VertiMeeter's own superset: the same 28-byte header with the magic "OBAN", the same PCM bytes, plus codecs of our own. It never uses VB-Audio's closed codecs, names or logos. Packet format: docs/OBAN_PACKET_FORMAT.md.

Install: `sudo pacman -S opus`, then rebuild the engine (`./install.sh` or `cd engine && cargo build --release`).

**Receive** (Virtual Inputs `[ + v ]` > Network Input (OBAN) strip, or Network (VBAN) strip). Click the box under the strip:
- `<stream name>`: empty = any stream, else only that name (max 16 characters).
- `<port>`: OBAN default 6990, VBAN default 6980.
- `[sender IP]`: only accept this sender. Empty = the first sender wins and others are ignored until it is silent for 3 s.
- `[ Accept v ]` (OBAN): Auto (everything), PCM only, Coded streams only.
- `<jitter buffer>`: 0-500 ms. Bigger survives a rough network, adds delay. OBAN default 40 ms, VBAN 20 ms.
- `[x] Answer VBAN device scans`: lets VBAN tools find the input. Off by default.
- `[x] Accept VBAN-TEXT remote commands`: `Strip[n].Gain=-3;Bus[1].Mute=1;` (stream name Command1; strips also .Solo and .Mono). Needs the sender IP filter. Off by default.
- `[ LISTEN ]` starts the receiver. The strip shows the live status; hover it for the full line.

**Send** (right-click a bus name > Network out (OBAN / VBAN)...). Host, port and stream name are only what you type; nothing is built in.

| Codec | Receivers | About |
| --- | --- | --- |
| VBAN PCM | Voicemeeter, VBAN apps, VertiMeeter | 1.5 Mbps at 48 kHz 16-bit stereo. Use this to talk to Voicemeeter |
| OBAN PCM | VertiMeeter | same bytes as VBAN PCM |
| OBAN Opus music | VertiMeeter | 6-510 kbps (128 default), 5 / 10 / 20 / 40 ms frames |
| OBAN Opus voice | VertiMeeter | voice tuned, loss protection on, try 24-48 kbps |
| OBAN VFLAC (VERTIFLAC) | VertiMeeter | bit-exact 16 or 24-bit, roughly 40% smaller than PCM on music |
| OBAN mu-law | VertiMeeter | 8-bit voice, 8 kHz mono is about 64 kbps |

- Delay is about 40 ms on a LAN (10 ms Opus frames, 20 ms buffer) and about 100 ms on the internet (defaults).
- Opus voice recovers lost packets best; Opus music relies on concealment, so keep the jitter buffer a bit larger on bad links.
- There is no encryption or login in VBAN or OBAN. Use it over a private network such as Tailscale, never open the port to the internet.

**Over Tailscale** (no port forwarding needed):
1. Both machines on the same tailnet. On the receiver run `tailscale ip -4` to get its address.
2. Receiver: add a Network Input (OBAN) strip, set `<stream name>` (for example Mix), `<port>` 6990, `[sender IP]` = the sender's Tailscale address, press LISTEN.
3. Sender: bus name right-click > Network out. Host = the receiver's Tailscale address, port 6990, the same stream name, Codec = OBAN Opus music (or lossless on a fast link). Press SEND.

**Interop with Voicemeeter / VBAN apps**: pick VBAN PCM on the sender, and on the receiving side use a Network (VBAN) strip or a normal Voicemeeter incoming stream. Voicemeeter's VBCA / VBCV codecs are closed and cannot be decoded; a sender using them shows "not supported - ask them to switch the sender to PCM" on the strip, and nothing is sent back. VBAN has no way to tell a Voicemeeter sender anything, so tell them yourself ([ Copy 'use PCM' note ]).

**Limits**: 8 receivers and 4 senders at once; buses are stereo (streams with more channels use the first two); VBAN-MIDI / serial, VBAN-FRAME, chat and the RT packet service are not implemented.

**Big files**: network audio is streamed in fixed-size buffers, so a 60 MB or 60 GB WAV plays the same way: play it in the media player, send that bus. Raw PCM at 48 kHz 16-bit stereo is 1.5 Mbps, lossless about 0.9 Mbps, Opus 0.13 Mbps. test_oban.py checks 40000 garbage packets and a 12 s 384 kHz float stream with flat memory.
