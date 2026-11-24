# SCPI logic for the SHT45 Trinkey, kept OUT of code.py on purpose.
#
# The SAMD21 Trinkey (~32 kB RAM) cannot compile this much .py on-device
# (MemoryError at import). So this module is precompiled off-device to app.mpy
# with a version-matched mpy-cross (see build-mpy.sh); code.py is a two-line
# stub that imports it -- no on-device compilation, no RAM spike.
#
# Uses adafruit_sht4x + neopixel, which are FROZEN into the Trinkey firmware
# (no install needed, negligible RAM). neopixel's own `brightness` is used.
# Commands: *IDN? *RST *CLS  MEAS? MEAS:TEMP? MEAS:HUM?
#   CALC:HUM:LOW|HIGH|WARN <v> (set) / ...? (query)  CALC:HUM:STAT?
#   DISP:BRIG <0..1> (LED brightness) / DISP:BRIG?    SYST:ERR?
# LED: green glow in band, amber faster glow near a limit, red blink outside.
# Keywords abbreviate per SCPI (UPPER part is the short form).

import gc
import math
import time

import board
import neopixel
import usb_cdc

import adafruit_sht4x


IDN = "MYOSS,SHT45-Trinkey,0001,1.0"


def _load_settings(path):
    # os.getenv()/settings.toml is not built into the SAMD21 firmware, so parse
    # the file ourselves: plain "KEY = VALUE" lines, '#' comments ignored.
    cfg = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line[0] == "#" or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.split("#", 1)[0].strip()
    except OSError:
        pass
    return cfg


_SETTINGS = _load_settings("/settings.toml")


def _envf(name, default):
    try:
        return float(_SETTINGS[name])
    except (KeyError, ValueError):
        return default


# Thresholds (%). Defaults from settings.toml; set over SCPI at runtime.
LOW = _envf("HUM_LOW_DEFAULT", 35.0)
HIGH = _envf("HUM_HIGH_DEFAULT", 70.0)
WARN = _envf("HUM_WARN_DEFAULT", 5.0)
DEFAULTS = (LOW, HIGH, WARN)
# Background read interval; clamped so the LED refreshes >=1x/min without SCPI.
PERIOD = max(1.0, min(_envf("MEASURE_INTERVAL", 5.0), 60.0))
BRIGHT = max(0.0, min(_envf("BRIGHTNESS", 0.2), 1.0))  # LED brightness 0..1
DEF_BRIGHT = BRIGHT

GREEN = (0, 255, 0)
AMBER = (255, 170, 0)
RED = (255, 0, 0)
GLOW_T = 3.0   # green breath (s)
WARN_T = 0.8   # amber breath (s), faster = urgent
BLINK_T = 0.6  # red on/off (s)
FLOOR = 0.15   # dimmest breath level

serial = usb_cdc.data or usb_cdc.console
serial.timeout = 0

i2c = board.I2C()
sht = adafruit_sht4x.SHT4x(i2c)
sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION

try:
    _pixel = neopixel.NeoPixel(
        board.NEOPIXEL, 1, brightness=BRIGHT, auto_write=True
    )
except Exception:
    _pixel = None

hum = None  # last humidity; None = no reading / sensor error


def read():
    global hum
    t, h = sht.measurements  # (temperature_c, relative_humidity_pct)
    hum = 0.0 if h < 0 else 100.0 if h > 100 else h
    return t, hum


def _set(c):
    if _pixel is not None:
        _pixel[0] = c  # neopixel handles colour order + brightness


def _glow(now, c, period):
    lvl = FLOOR + (1 - FLOOR) * (math.sin(now * 6.2831853 / period) + 1) / 2
    _set((int(c[0] * lvl), int(c[1] * lvl), int(c[2] * lvl)))


def zone(h):
    if h is None or h < LOW or h > HIGH:
        return "OUT"
    if h < LOW + WARN or h > HIGH - WARN:
        return "WARN"
    return "IN"


def indicate(now):
    if _pixel is None:
        return
    z = zone(hum)
    if z == "IN":
        _glow(now, GREEN, GLOW_T)
    elif z == "WARN":
        _glow(now, AMBER, WARN_T)
    else:
        _set(RED if (now % BLINK_T) < BLINK_T / 2 else (0, 0, 0))


def _ab(full, tok):
    # tok matches SCPI keyword `full` by prefix, min = its short form length.
    short = sum(1 for ch in full if ch.isupper())
    return full.upper().startswith(tok) and len(tok) >= short


def _is(toks, *spec):
    if len(toks) != len(spec):
        return False
    return all(_ab(s, t) for s, t in zip(spec, toks))


def _pct(a):
    v = float(a)
    if not 0 <= v <= 100:
        raise ValueError("range 0..100")
    return v


def handle(line):
    global LOW, HIGH, WARN, BRIGHT
    p = line.split(None, 1)
    head = p[0].upper()
    arg = p[1].strip() if len(p) > 1 else None
    q = head.endswith("?")
    if q:
        head = head[:-1]
    t = head.split(":")

    if head == "*IDN":
        return IDN
    if head == "*RST":
        LOW, HIGH, WARN = DEFAULTS
        BRIGHT = DEF_BRIGHT
        if _pixel is not None:
            _pixel.brightness = BRIGHT
        return None
    if head == "*CLS":
        return None
    if q and (_is(t, "MEASure") or _is(t, "READ")):
        a, b = read()
        return "%.2f,%.2f" % (a, b)
    if q and _is(t, "MEASure", "TEMPerature"):
        return "%.2f" % read()[0]
    if q and _is(t, "MEASure", "HUMidity"):
        return "%.2f" % read()[1]
    if _is(t, "CALCulate", "HUMidity", "LOW"):
        if q:
            return "%.2f" % LOW
        LOW = _pct(arg)
        return None
    if _is(t, "CALCulate", "HUMidity", "HIGH"):
        if q:
            return "%.2f" % HIGH
        HIGH = _pct(arg)
        return None
    if _is(t, "CALCulate", "HUMidity", "WARN"):
        if q:
            return "%.2f" % WARN
        WARN = _pct(arg)
        return None
    if q and _is(t, "CALCulate", "HUMidity", "STATe"):
        return zone(hum)
    if _is(t, "DISPlay", "BRIGhtness"):
        if q:
            return "%.2f" % BRIGHT
        BRIGHT = max(0.0, min(float(arg), 1.0))
        if _pixel is not None:
            _pixel.brightness = BRIGHT
        return None
    if q and _is(t, "SYSTem", "ERRor"):
        return '0,"No error"'
    return '-113,"Undefined header"'


def run():
    global hum
    gc.collect()
    try:
        read()
    except Exception:
        pass
    buf = b""
    nxt = time.monotonic()
    conn = serial.connected
    print("data port connected:", conn)
    while True:
        now = time.monotonic()
        c = serial.connected
        if c != conn:
            conn = c
            print("data port", "connected" if c else "disconnected")
        if now >= nxt:
            nxt = now + PERIOD
            try:
                t, h = read()
                print("T=%.2f C  RH=%.2f %%  [%s]" % (t, h, zone(h)))
            except Exception as e:
                hum = None
                print("sensor error:", e)
            gc.collect()
        indicate(now)
        n = serial.in_waiting
        if n:
            # Accept CR, LF or CRLF as line endings (CRLF -> empty line, skipped)
            buf += serial.read(n).replace(b"\r", b"\n")
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                try:
                    cmd = ln.decode().strip()
                except Exception:
                    cmd = ""
                if not cmd:
                    continue
                try:
                    r = handle(cmd)
                    print("SCPI <", cmd, "->", r if r is not None else "(ok)")
                    if r is not None:
                        serial.write((r + "\n").encode())
                except Exception as e:
                    print("SCPI <", cmd, "!!", e)
                    serial.write(('-100,"%s"\n' % e).encode())
        time.sleep(0.01)
