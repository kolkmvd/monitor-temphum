#!/usr/bin/env bash
#
# Compile app.py -> app.mpy with a version-matched mpy-cross and deploy the
# firmware to the Trinkey. Needed because the SAMD21 cannot compile this much
# .py on-device (MemoryError at import).
#
# Usage:
#   ./build-mpy.sh /mnt/d        # path to the mounted CIRCUITPY drive
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIVE="${1:-}"

if [ -z "$DRIVE" ] || [ ! -d "$DRIVE" ]; then
    echo "Usage: $0 /path/to/CIRCUITPY" >&2
    exit 1
fi
if [ ! -f "$DRIVE/boot_out.txt" ]; then
    echo "No boot_out.txt in $DRIVE -- is that the CIRCUITPY drive?" >&2
    exit 1
fi

# 1. Detect the CircuitPython version, e.g. "10.3.0" from boot_out.txt.
VER="$(grep -oE 'CircuitPython [0-9]+\.[0-9]+\.[0-9]+' "$DRIVE/boot_out.txt" \
       | head -1 | awk '{print $2}')"
if [ -z "$VER" ]; then
    echo "Could not parse a CircuitPython x.y.z version from boot_out.txt." >&2
    echo "Contents:" >&2
    cat "$DRIVE/boot_out.txt" >&2
    exit 1
fi
echo "CircuitPython $VER on the Trinkey."

# 2. Fetch the matching mpy-cross (cached per version).
CACHE="$HOME/.cache/monitor-temphum"
mkdir -p "$CACHE"
MPY="$CACHE/mpy-cross-$VER"
if [ ! -x "$MPY" ]; then
    URL="https://adafruit-circuit-python.s3.amazonaws.com/bin/mpy-cross/linux-amd64/mpy-cross-linux-amd64-$VER.static"
    echo "Downloading mpy-cross $VER ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fSL "$URL" -o "$MPY"
    else
        wget -O "$MPY" "$URL"
    fi
    chmod +x "$MPY"
fi

# 3. Compile and deploy.
echo "Compiling app.py -> app.mpy ..."
"$MPY" "$HERE/app.py" -o "$CACHE/app.mpy"

echo "Deploying to $DRIVE ..."
cp "$CACHE/app.mpy" "$DRIVE/app.mpy"
cp "$HERE/code.py" "$DRIVE/code.py"
cp "$HERE/boot.py" "$DRIVE/boot.py"
cp "$HERE/settings.toml" "$DRIVE/settings.toml"
rm -f "$DRIVE/app.py"  # drop any stale source so the .mpy is used

echo "Done. Eject and replug the Trinkey to run."
