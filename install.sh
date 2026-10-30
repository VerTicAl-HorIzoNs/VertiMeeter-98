#!/usr/bin/env bash
# VertiMeeter installer (Arch). Builds the native engine and sets up a local venv. No sudo needed for those.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [VertiMeeter] [$1]: $2"; }

MISSING=""
for p in pipewire pipewire-jack pipewire-pulse wireplumber python rust ffmpeg opus cdparanoia; do
    pacman -Q "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
    log WARN "missing system packages:$MISSING"
    log WARN "run: sudo pacman -S --needed$MISSING"
    exit 1
fi

log INFO "building native engine (cargo --release)"
( cd "$DIR/engine" && cargo build --release )

log INFO "creating venv"
python3 -m venv --system-site-packages "$DIR/.venv"
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt"
chmod +x "$DIR/vertimeeter.sh"

mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons"
cp "$DIR/assets/vm98.png" "$HOME/.local/share/icons/vertimeeter.png"
cat > "$HOME/.local/share/applications/vertimeeter.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=VertiMeeter
Comment=Voicemeeter-style mixer for PipeWire
Exec=$DIR/vertimeeter.sh
Icon=$HOME/.local/share/icons/vertimeeter.png
StartupWMClass=vertimeeter
Terminal=false
Categories=AudioVideo;Audio;Mixer;
EOF
log OK "installed. launch with ./vertimeeter.sh or from the app menu"
