# VertiMeeter '98 — Voicemeeter Potato parity matrix

Source: VB-Audio's Potato page, Potato user manual (to the VBAN section), Voicemeeter overview, Macro Buttons page.
Not covered by the manual text I could reach: exact System Settings option list, MIDI mapping modes, Patch Insert details, Macro script command list. Those are marked (partial).

Status key: MOCK = in the HTML mockup v4. ENGINE = needs DSP/engine work. LINUX = how it maps on PipeWire.

## 1. I/O layout
- 5 hardware inputs + 3 virtual inputs (VAIO, AUX, VAIO3). Ours: dynamic count, same defaults.
- Outputs: A1-A5 hardware, B1-B3 virtual. Ours: dynamic count up to A1-A8 and B1-B8, same defaults.
- 3 virtual ASIO I/O (8ch, up to 4 clients) + virtual insert (34ch). LINUX: JACK ports on the VertiMeeter client, named so Ardour/Carla/OBS see them. The 34ch insert = JACK send/return port bank.
- External FX send/return x2 (AUX). LINUX: JACK port pairs.
- Sample rates 32k-192k, driven by A1. LINUX: PipeWire clock rate + quantum; PipeWire resamples per device.
- Driver types (WDM/KS/MME/ASIO). LINUX: not applicable; one backend (PipeWire/JACK). Menu shows "PipeWire" and the quantum instead.
- Up to 8 channels per bus (5.1/7.1). ENGINE: strips and buses become 8-channel internally, not stereo.
- Internal clock when no device. ENGINE.
- Physical output sync delay (System Settings). ENGINE (per-bus delay already exists).
- Engine restart + auto-restart when A1 disconnects. ENGINE.
- Check driver installation. LINUX: "Check PipeWire setup" (pipewire, wireplumber, pipewire-jack, pactl present).

## 2. Hardware input strip
- Device select box (empty = receives VBAN/JACK). MOCK.
- Intellipan panel with 3 modes (right-click cycles): MOCK.
  - Color panel (2D): X = brightness, Y = reverb/color on 3 bands. ENGINE: 3 shelf/peaking filters + small reverb send.
  - 3D panel (binaural): X/Y position, stereo only. ENGINE: HRTF convolution (SOFA/HRIR file via scipy) with simple ITD/ILD fallback. This gives the "in front of me" and "only in the right ear" effects.
  - Modulation panel: chorus/feedback modulation. ENGINE: chorus + flanger.
  - 3 indicator dots go orange when a panel is off-neutral. MOCK.
- Compressor knob (simple, auto make-up) + detail dialog: input gain, ratio, threshold, attack, release, knee, output gain, auto make-up. MOCK/ENGINE (ours is a block-peak comp now; needs a real sample-accurate one).
- Gate knob + detail: threshold, damping max, BP sidechain, attack, hold, release. MOCK/ENGINE.
- Denoiser knob (0-10) + detail: noise floor remover. LINUX: RNNoise (librnnoise/noise-suppression-for-voice) or spectral gate.
- Voice modeler inside denoiser detail: enable, dry/wet -100..+100, pitch -12..+12 st, formant lo/med/hi -12..+12 st, formant group, 8 presets. LINUX: rubberband (pitch/formant) or WORLD vocoder.
- 6-cell parametric EQ per channel (-36..+18 dB), opened from the EQ button. MOCK. ENGINE: 6 biquads/channel.
- Strip limiter -40..+12 dB, two types (yellow instant, green 5 ms lookahead). MOCK/ENGINE.
- FX send knobs: Reverb, Delay, with POST toggle (pre/post fader). MOCK.
- External FX send/return knobs (x2). MOCK.
- Gain fader, MONO, SOLO, MUTE. MOCK.
- Bus assign buttons A1-A8 left, B1-B8 right. DONE 0.11.0.
- Strip menu (right-click header): reset, copy/paste, load/save XML per effect or whole strip. MOCK menu.
- Mono click twice = swap channels? (BUS only per manual; strip has MONO.) MOCK.

## 3. Virtual input strip
- Everything above except hardware device box and 6-cell EQ. Gets a 3-band EQ (bass/mid/treble) instead. MOCK.
- 5.1 pan pot: Front L/C/R, Rear L/R. Right-click modes: Normal, Mono, Repeat Mono, Repeat Stereo, Mix Down, Up Mix, Only. MOCK/ENGINE.
- M.C. button (mute center, for removing movie dialog). MOCK/ENGINE.
- K karaoke button on AUX strip: K-m, K-1, K-2, Kv (voice-band filter 200-4000 Hz). MOCK/ENGINE.
- Application list: first 4 apps with per-app volume, mute, meter; right-click expands to 32. LINUX: pactl sink-input list, per-app volume via pactl set-sink-input-volume/mute. MOCK.
- Latency setting per virtual input (right-click caption). LINUX: node.latency on the null sink.
- Windows-volume-control toggle. LINUX: n/a (PipeWire volume is always honored).

## 4. Special FX section (shared send/return)
- Reverb: JustVerb III equivalent. 10x3 presets, 3-band EQ, DRY, WET, pre-delay (to 1000 ms), E.Ref, DECAY 50-200%, BYP. LINUX: own reverb (Freeverb/zita-style FDN) in the engine. MOCK dialog.
- Delay: MultiTap 8 delay. 8 stereo taps, per-tap gain/pan/LPF/HPF, gain+pan timelines, time ratio, DRY, WET, pre-delay, balance, feedback, SET NOTE, TAP tempo, SCALE FIT, FIX DELAY, AUTO FIT, BYPASS. MOCK dialog (reduced timeline UI first).
- C5 multiband compressor (replaces delay when toggled): 5 bands, 4 crossovers, per-band gain/threshold/ratio/attack/release/knee/out gain/limit/distortion/enable/solo/mute, LINK modes ABS/REL/independent, output gain, auto make-up, bypass, save/load. MOCK dialog.
- Return knobs per bus (Reverb, Delay, External FX return 1/2). MOCK. LINUX: each bus has its own reverb and delay fed only by the strips routed to it (0.10.4), so effects never leak to unrouted buses or vMics.

## 5. Master section (per bus)
- Hardware outs A1-A8, virtual B1-B8. Dynamic. DONE 0.11.0.
- SEL button: edit that bus's sub-mix on the input faders. CTRL+click multi-select. Right-click bus buttons = overview of all sub-mixes. MOCK/ENGINE (needs per-strip per-bus gain, not one gain).
- MONO (click twice = swap L/R), EQ on/off + master PEQ (6 cells x 8 channels, channel select, FLAT, A/B memories, CH COPY, COPY ALL, Q/gain/freq, per-channel trim/delay, save/load XML, EQ-button colors green/red/yellow/black). MOCK dialog.
- MUTE, gain fader, bus limiter -20..0 dB, integrated peak remover. MOCK.
- 14 bus modes: Normal, Mix Down A, Mix Down B, Stereo Repeat, Composite, Up Mix TV, Up Mix 2.1, Up Mix 4.1, Up Mix 6.1, Left Only, Right Only, Center Only, LFE Only, Rear Only. MOCK/ENGINE.
- MON button per bus (monitor to chosen output via System Settings). MOCK.
- Composite patch: build the 8 channels of a bus from any pre/post-fader input. MOCK dialog.
- Delay per channel. MOCK.

## 6. Recorder / media (cassette)
- Play: WAV, AIFF, MP3, M4A, MP4, MOV, AVI, WMA, WMV. Ours: anything ffmpeg reads, video = audio only, multiple files. Our media section is bigger than theirs. MOCK.
- Record: WAV, BWF, AIFF, MP3 (32-320 kbps). Source = pre-fader inputs or post-fader buses. Options: pre-record buffer (20 s default), file prefix, multitrack (one file per channel), play on load, loop, playback gain, stop-after duration. MOCK dialog.
- Cassette UI: tape counter, sample rate / channel display, rewind, ffwd, record, stop, play. MOCK (Potato theme).

## 7. VBAN (network audio)
- 8 audio in + 8 audio out streams, up to 8ch, 11025 Hz-96 kHz, 16/24 bit PCM, UDP default port 6980. LINUX: implement the open VBAN protocol (Python socket). Works over Tailscale since the dorm has no port forwarding. MOCK.
- Stream identity = name + source IP + port. Right-click to pick a detected stream. Hostnames ok. Broadcast 255 (wired). MOCK.
- Network quality FAST/SLOW, error LEDs: overload, corrupt, disorder, missing, underrun. MOCK.
- VBAN-MIDI in/out, VBAN-TEXT (commands, scripts), VBAN-FRAME (screen), VBAN-Chat, VBAN-Ping, user name + color, RT packet service (several remote controllers). MOCK dialog; FRAME/screen = later.
- 0.10.0 DONE: Network Input (OBAN) strip + Network out (OBAN / VBAN) on every bus. VBAN PCM (all formats, 6 kHz-705.6 kHz in), OBAN PCM, Opus music / voice, lossless, mu-law. Jitter buffer, reorder, loss concealment, sender lock / IP filter, status line, 8 receivers / 4 senders. VBAN-TEXT remote control (Strip[n] / Bus[n] Gain, Mute, Solo, Mono), PING answer. NOT done: VBAN-MIDI / serial, VBAN-FRAME, chat, RT packets, VBCA / VBCV (closed codecs), more than 2 channels.

## 8. Control surfaces
- Macro Buttons: up to 80 buttons, PUSH and 2-position toggle, name/color/image, press+release scripts. Triggers: keyboard, mouse, MIDI, HID, gamepad, GPIO, audio level, VBAN-TEXT scripts. Actions: Voicemeeter commands, MIDI out, keyboard/mouse events, launch programs, DMX. MOCK window. LINUX: evdev for keyboard/HID/gamepad (works on Wayland), mido/rtmidi for MIDI, xdotool/ydotool for output events. (script command list = partial)
- MIDI mapping dialog: learn mode for faders, mute, solo, M.C., feedback to the surface. Limit remote gain to 0 dB option. MOCK.
- Hook keyboard volume keys to A1 / A2 / Strip 1. MOCK.
- Preset scenes: 64, F1-F24 recall, name/comment/copy/paste, save/load. Scenes hold mix + FX only, not devices/MIDI/VBAN. MOCK.
- Streamer view: only labeled strips/buses, MON, slider modes NO LINK/ABS/REL, DIRECT or up to 4 VBAN connections to remote machines. MOCK window.
- Windows Audio Map. LINUX: PipeWire graph map (nodes, apps, links), zoom, refresh. MOCK window.
- Command-line control + API (Python/remote). ENGINE (later).

## 9. Main menu items
- Restart audio engine, release audio file, load/save/reset settings, load settings at startup, system tray, run at startup, show on launch, always on top, lock GUI, run Macro Buttons/Streamer on launch, hook keyboard keys, limit remote gain, contextual help, preset scene on startup, dialogs (System Settings, Recording Options, MIDI Mapping, VBAN), check install, license, about, shutdown. MOCK.

## 10. Mouse and keyboard behaviors
- Double-click = reset control. CTRL+right-click = type value. SHIFT+click = fine slider. Right-click = detail view/back. CTRL+click limiter = yellow/green. CTRL+wheel = adjust sub-mix. Enter/CTRL+click recall preset. F1-F24 presets. MOCK.

## 11. Things Voicemeeter does NOT have that we add
- Dynamic strip/bus counts with the scroll slider and [ - ] [ + v ].
- Live device lists in the [ + v ] dropdown.
- Multiple simultaneous media files with per-file per-bus routing.
- Draggable resize borders and a fully hand-placed Custom layout (foobar-style).
- Separate panes for hardware inputs, virtual inputs, hardware outputs, virtual outputs, special FX, media. Themes: Default VertiMeeter '98 (purple), Potato-style, Custom.
- Plugin hosting (LV2/LADSPA, VST via Carla) in insert slots.

## 12. Hard parts and risks
- 8-channel internal audio, per-strip-per-bus gains, 6-band EQ x 8 strips x 8 buses, binaural convolution, reverb, 8-tap delay, C5, denoiser, pitch shift all in one callback is too heavy for pure Python. Plan: move the mixing core to C or Rust, keep PyQt GUI in Python (as noted in earlier plan). The mockup does not depend on this decision, but the real build does.
- Voice modeler (pitch + formants) adds latency and CPU. Expect it to be per-strip optional.
- Denoiser and Voice Modeler add latency in Voicemeeter too ("any non-zero value adds latency").
- Not portable: ASIO drivers, WDM/KS/MME. Replaced by PipeWire/JACK as noted.
- Global hotkeys on KDE Wayland need evdev (input group) or KDE shortcuts over D-Bus.

## 13. Build order proposal
1. Mockup v4 (this round): panes, Potato theme, all dialogs listed MOCK.
2. Core engine rewrite (C/Rust or optimized numpy): 8ch, per-strip sends, PEQ, comp, gate, limiter. 
3. Dynamic strips/buses, media player, recorder.
4. Intellipan, Special FX (reverb, delay, C5), denoiser, voice modeler.
5. VBAN, MIDI, macros, scenes, streamer view.
6. Plugins (LV2/LADSPA, Carla).
