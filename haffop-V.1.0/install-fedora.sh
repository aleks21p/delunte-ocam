#!/bin/sh
set -eu

case "$(uname -s)" in
    Linux) ;;
    *) echo "This installer is for Fedora/Linux." >&2; exit 1 ;;
esac

if [ ! -r /etc/fedora-release ]; then
    echo "This installer targets Fedora. For Debian/Ubuntu, use the .deb package." >&2
    exit 1
fi

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
BIN_HOME=${XDG_BIN_HOME:-"$HOME/.local/bin"}
APP_DIR="$DATA_HOME/haffop"
VENV="$APP_DIR/venv"
DESKTOP_DIR=${XDG_DATA_HOME:-"$HOME/.local/share"}/applications

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 is required. Install it with: sudo dnf install python3" >&2
    exit 1
fi

mkdir -p "$APP_DIR" "$BIN_HOME" "$DESKTOP_DIR"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$HERE/requirements.txt"

rm -rf "$APP_DIR/haffop"
cp -R "$HERE/haffop" "$APP_DIR/haffop"
cat > "$BIN_HOME/haffop" <<EOF
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
export PYTHONPATH
exec "$VENV/bin/python" -m haffop "\$@"
EOF
chmod 0755 "$BIN_HOME/haffop"

export HAFFOP_APP_DIR="$APP_DIR"
export HAFFOP_LAUNCHER="$BIN_HOME/haffop"
export HAFFOP_DESKTOP="$DESKTOP_DIR/haffop.desktop"
python3 - <<'PY'
import os

def desktop_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

launcher = desktop_quote(os.environ["HAFFOP_LAUNCHER"])
icon = os.path.join(os.environ["HAFFOP_APP_DIR"], "haffop", "resources", "haffop.svg")
with open(os.environ["HAFFOP_DESKTOP"], "w", encoding="utf-8") as f:
    f.write(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Haffop\n"
        "GenericName=Hardware Monitor and Performance Tuner\n"
        "Comment=View hardware and live temperatures; tune performance\n"
        f"Exec={launcher}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=System;Monitor;HardwareSettings;\n"
        "Keywords=hardware;temperature;cpu;gpu;ram;performance;\n"
        "StartupWMClass=haffop\n"
    )
PY

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo "Haffop is installed for your user."
echo "Launch it from the applications menu, or run: $BIN_HOME/haffop"
case ":${PATH:-}:" in
    *":$BIN_HOME:"*) ;;
    *) echo "If needed, add $BIN_HOME to PATH to use the haffop command." ;;
esac
