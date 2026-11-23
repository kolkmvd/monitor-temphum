# Tiny entry point. All logic lives in app.mpy, precompiled off-device because
# the SAMD21 Trinkey cannot compile that much .py on-device (MemoryError).
# Build + deploy with build-mpy.sh. Keep this file minimal.
import app

app.run()
