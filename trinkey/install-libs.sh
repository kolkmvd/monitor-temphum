#!/usr/bin/env bash
#
# Install the CircuitPython libraries for the SHT45 Trinkey onto the connected
# CIRCUITPY drive, using Adafruit's `circup`. The Trinkey must be plugged in and
# its CIRCUITPY drive mounted.
#
# Usage:
#   ./install-libs.sh                 # auto-detect the CIRCUITPY drive
#   ./install-libs.sh /media/me/CIRCUITPY   # or point at it explicitly
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ="$HERE/requirements-circup.txt"

# 1. Make sure circup is available (install per-user if missing).
if ! command -v circup >/dev/null 2>&1; then
    echo "circup not found; installing it..."
    if command -v pipx >/dev/null 2>&1; then
        pipx install circup
    else
        python3 -m pip install --user circup
        export PATH="$HOME/.local/bin:$PATH"
    fi
fi

if ! command -v circup >/dev/null 2>&1; then
    echo "circup is still not on PATH. Add ~/.local/bin to PATH and retry." >&2
    exit 1
fi

# 2. Optional explicit path to the CIRCUITPY drive (circup auto-detects if omitted).
path_arg=()
if [ "${1:-}" != "" ]; then
    path_arg=(--path "$1")
fi

# 3. Install the libraries (dependencies are resolved automatically).
echo "Installing CircuitPython libraries from $(basename "$REQ")..."
circup "${path_arg[@]}" install -r "$REQ"

echo "Done. Libraries are now in CIRCUITPY/lib/."
echo "Tip: 'circup ${path_arg[*]} freeze' lists what is installed on the board."
