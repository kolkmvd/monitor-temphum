#!/usr/bin/env bash
#
# Install the CircuitPython libraries for the SHT45 Trinkey onto the connected
# CIRCUITPY drive, using Adafruit's `circup`. The Trinkey must be plugged in and
# its CIRCUITPY drive mounted.
#
# Usage:
#   ./install-libs.sh                        # auto-detect the CIRCUITPY drive
#   ./install-libs.sh /media/me/CIRCUITPY    # point at it explicitly
#   ./install-libs.sh --path /mnt/d          # same, circup-style
#
# Note (WSL): auto-detect does not work; the Trinkey shows up as a Windows
# drive letter. Mount it first, e.g. `sudo mount -t drvfs D: /mnt/d`, then pass
# that path. Verify with `ls` -- it should contain boot_out.txt, code.py, lib/.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQ="$HERE/requirements-circup.txt"

# 1. Resolve a `circup` command without touching a PEP 668 "externally managed"
#    system Python: prefer an existing circup, then pipx, then a private venv.
if command -v circup >/dev/null 2>&1; then
    CIRCUP="circup"
elif command -v pipx >/dev/null 2>&1; then
    echo "Installing circup with pipx..."
    pipx install circup >/dev/null
    export PATH="$HOME/.local/bin:$PATH"
    CIRCUP="circup"
else
    VENV="$HOME/.cache/monitor-temphum/circup-venv"
    if [ ! -x "$VENV/bin/circup" ]; then
        echo "No circup/pipx found; creating a private venv at $VENV ..."
        if ! python3 -m venv "$VENV" 2>/dev/null; then
            echo "Could not create a virtualenv. Install one of:" >&2
            echo "  sudo apt install python3-venv     # then re-run" >&2
            echo "  sudo apt install pipx             # then re-run" >&2
            exit 1
        fi
        "$VENV/bin/pip" install --quiet --upgrade pip
        "$VENV/bin/pip" install --quiet circup
    fi
    CIRCUP="$VENV/bin/circup"
fi

echo "Using circup: $CIRCUP"

# 2. Optional explicit path to the CIRCUITPY drive (circup auto-detects if
#    omitted). Accept both a bare path and circup-style `--path <dir>`.
path_arg=()
target=""
if [ "${1:-}" = "--path" ]; then
    target="${2:-}"
elif [ "${1:-}" != "" ]; then
    target="$1"
fi
if [ -n "$target" ]; then
    if [ ! -d "$target" ]; then
        echo "Path '$target' does not exist or is not mounted." >&2
        exit 1
    fi
    if [ ! -f "$target/boot_out.txt" ]; then
        echo "Warning: no boot_out.txt in '$target' -- is that really the" \
             "CIRCUITPY drive? circup needs it to pick matching libraries." >&2
    fi
    path_arg=(--path "$target")
fi

# 3. Install the libraries (dependencies are resolved automatically).
echo "Installing CircuitPython libraries from $(basename "$REQ")..."
"$CIRCUP" "${path_arg[@]}" install -r "$REQ"

echo "Done. Libraries are now in CIRCUITPY/lib/."
echo "Tip: '$CIRCUP ${path_arg[*]} freeze' lists what is installed on the board."
