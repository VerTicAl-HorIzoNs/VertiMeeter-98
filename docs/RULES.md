# Rules for any chat working on VT-Audio Software / VertiMeeter

These are Vertical's standing rules. Follow them every time.

## How to work with him
- Plan first, then wait for an explicit "build it" / "let's code" / "write the code" before writing code. A clear direct instruction ("do that", "patch if you want") counts. Small bug fixes he asks for directly can just be done.
- Direct answers, bullets, fewer words. Casual tone is fine. No walls of prose. Minimal, literal formatting (he finds heavy markdown confusing).
- Put every command and anything copy-pasteable in fenced code blocks. Give full terminal commands, no shorthand.
- Assume Arch Linux. Measurements in inches if any come up.
- Ownership wording: everything is his (product, project, design) except the code, which is Claude's ("I have the code, you have everything else").
- Push back with facts when he is wrong, and own it plainly when I am wrong. Do not oversell ("close" is not "1:1").
- Do not ask questions you can answer yourself. Ask one short question only when a wrong guess is costly.

## Where code lives and how it ships
- All code Claude writes on his machine goes under `~/Documents/scripts...malware...linux/_CLAUDE_/`. Things made for him: `_CLAUDE_/_FOR_VERTICAL_/<project>/`.
- Every project needs README.md, a changelog (CHANGELOG.md), and requirements.txt for Python deps.
- Version x.y.z: z = minor update, y = big change, x = massive rework and only when he says so.
- Deliver as a zip. Top folder = project name. Exclude dist, .venv, engine/target, _shots, _old, __pycache__.
- Work on his files only through desktop-commander tools (start_process, read_file, write_file, edit_block). The sandbox Edit tool cannot reach them.
- Standard log/CLI output: `[{timestamp}] [{name}] [{type}]: {message}`, colored where possible. Optional spinner frames: `_[^,^]| ` -> `\[^,^]/` -> `|[^,^]_` -> `\[^,^]/` (the body `[^,^]` never moves).

## UI rules
- Win9x look, dark purple on black, V motif. Sliders must be clearly visible (visible track, fill and handle).
- Spec notation: `<field>` data filled at runtime, `[ Button ]` (spaces inside), `[optional]` (no spaces), `{name}` template variable, `[x]` / `[ ]` checkbox, `( )` / `(o)` radio, `[ field v ]` dropdown. Remind him if he mixes them up.
- Mockup first (1:1 faithful HTML) and get approval before building a new real GUI.
- NEVER hard-code a host, IP or server address in code, mockups or tests. Addresses come from config or user input.

## Safety rules for his machine
- His real VertiMeeter may be running (real engine socket `/run/user/1000/vertimeeter-engine.sock`). Never kill it, never run cleanup/sweep code or `pkill` patterns that could match it. Check `ps` first.
- Tests and screenshots must be isolated: env `VERTIMEETER_SOCK`, `VERTIMEETER_CLIENT`, `VERTIMEETER_CONFIG_DIR`, `VERTIMEETER_NOLINK=1`, `QT_QPA_PLATFORM=offscreen`. They must never play to his speakers or touch his real PipeWire graph. Stop every engine a test starts (atexit) and verify nothing is left behind.
- Do not use `pkill -f` with patterns that match your own shell command.
- Do not disable or delete things of his unless he says to. Deleting files: move to a `_to_delete/` or `_old/` folder instead.

## Legal / naming rules for the suite
- VBAN: the protocol spec is public, so VBAN compatibility is fine. The name "VBAN" is VB-Audio's trademark, so own extensions use OBAN.
- Everything else of VB-Audio is closed. Clone behavior only, from public information and own design. No decompiling, no copied code, icons, logos, screenshots, sounds, exact art, names or trademarks, no private file formats. Check the Voicemeeter Remote SDK license before using it. Never copy GPL code (e.g. quiniouben/vban) into this project.
- The Potato-style theme is an homage: sampled colors and own drawing, label it "Potato-style", never claim compatibility or affiliation.
- Never send or recognize-and-send VB-Audio's closed codec IDs (0x10, 0x20, 0xF0). Recognize and refuse only.
- Network features: no replies to unauthenticated audio packets (reflection risk), optional features default OFF, no encryption claims (recommend Tailscale).

## Quality bar
- Every parser in the engine is bounds-checked (`panic = "abort"`).
- Everything the app starts must die with the app. Check for leftovers after tests (`ps -eo pid,ppid,etimes,args | grep -E "[p]w-cli|[v]ertimeeter-engine|[p]w-cat"`).
- Run the relevant tests before delivering, look at a screenshot for any GUI change, and say honestly what was NOT verified (e.g. things not heard by ear, real Voicemeeter interop).
- Update README.md, CHANGELOG.md and docs with every change, and re-check docs against the code (do not claim features that are not there).
- Final message after a task: what came out, the zip, one next step if real. No recap of the steps.
