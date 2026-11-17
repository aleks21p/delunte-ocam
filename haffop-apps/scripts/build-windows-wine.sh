#!/bin/sh
# Runs INSIDE the docker.io/tobix/pywine container (Wine + Windows Python), see ../build-apps.sh.
# Cross-builds the Windows 11 edition Haffop.exe with PyInstaller.
#   /src = haffop-V.1.0 (read only)   /out = haffop-apps/windows
set -eu
WORK=/tmp/haffop-win
rm -rf "$WORK"; mkdir -p "$WORK"
cp -r /src/haffop /src/requirements.txt /src/packaging "$WORK/"
find "$WORK" -name '__pycache__' -type d -prune -exec rm -rf {} +
cd "$WORK"

echo "==> [windows] installing Python packages under Wine"
# Qt >= 6.8 DLLs don't load under the Wine in this image; 6.7 does and fully supports Windows 10/11
wine python -m pip install -q --upgrade pip 2>/dev/null
wine python -m pip install -q "PyQt6==6.7.1" "PyQt6-Qt6==6.7.*" "psutil>=5.9" pyinstaller nvidia-ml-py 2>&1 \
    | grep -v -e fixme -e notice || true
wine python -c "import PyQt6.QtWidgets, PyQt6.QtSvg, psutil" 2>&1 | grep -v fixme || true
wine python -c "import PyQt6.QtWidgets, PyQt6.QtSvg, psutil" 2>/dev/null \
    || { echo "PyQt6 does not import under Wine - aborting" >&2; exit 1; }

echo "==> [windows] rendering the icon"
ICON_ARGS=""
if QT_QPA_PLATFORM=offscreen wine python packaging/windows/make_icon.py haffop/resources/haffop.svg build/haffop.ico 2>/dev/null \
        && [ -s build/haffop.ico ]; then
    ICON_ARGS="--icon build/haffop.ico"
else
    echo "    (icon rendering failed under Wine - building without a custom icon)"
fi

echo "==> [windows] building Haffop.exe"
# --paths . : the entry script lives in packaging/windows, so the haffop package must be added explicitly
# shellcheck disable=SC2086
wine pyinstaller --noconfirm --clean --onefile --windowed --noupx --log-level WARN \
    --name Haffop --uac-admin $ICON_ARGS \
    --paths . \
    --add-data "haffop/resources;resources" \
    --collect-submodules haffop \
    packaging/windows/run_haffop.py 2>&1 | grep -v -e '^[0-9a-f]*:fixme:' -e '^[0-9a-f]*:err:' || true

test -s dist/Haffop.exe
# A Qt-less build is ~6 MB; a real one is well over 30 MB
[ "$(stat -c %s dist/Haffop.exe)" -gt 30000000 ] || { echo "Haffop.exe is too small - Qt was not bundled" >&2; exit 1; }
cp dist/Haffop.exe /out/Haffop.exe
echo "==> [windows] done:"; ls -lh /out/Haffop.exe
