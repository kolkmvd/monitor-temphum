# SCPI firmware for the Adafruit SHT45 Trinkey (SAMD21, ~32 kB RAM).
# Library-free to fit RAM: direct I2C to the SHT4x, neopixel_write for the LED.
# Commands: *IDN? *RST *CLS  MEAS? MEAS:TEMP? MEAS:HUM?
#   CALC:HUM:LOW|HIGH|WARN <v> (set) / ...? (query)  CALC:HUM:STAT?  SYST:ERR?
# LED: green glow in band, amber faster glow near a limit, red blink outside.
# Keywords abbreviate per SCPI (UPPER part is the short form).

import gc
import math
import os
import time

import board
import digitalio
import neopixel_write
import usb_cdc

IDN = "MYOSS,SHT45-Trinkey,0001,1.0"


def _envf(name, default):
    # settings.toml ints come back as str on CircuitPython >=10.2.0.
    try:
        return float(os.getenv(name))
    except (TypeError, ValueError):
        return default


# Thresholds (%). Defaults from settings.toml; set over SCPI at runtime.
LOW = _envf("HUM_LOW_DEFAULT", 40.0)
HIGH = _envf("HUM_HIGH_DEFAULT", 70.0)
WARN = _envf("HUM_WARN_DEFAULT", 5.0)
DEFAULTS = (LOW, HIGH, WARN)
# Background read interval; clamped so the LED refreshes >=1x/min without SCPI.
PERIOD = max(1.0, min(_envf("MEASURE_INTERVAL", 5.0), 60.0))

GREEN = (0, 80, 0)
AMBER = (90, 60, 0)
RED = (40, 0, 0)
GLOW_T = 3.0   # green breath (s)
WARN_T = 0.8   # amber breath (s), faster = urgent
BLINK_T = 0.6  # red on/off (s)
FLOOR = 0.15   # dimmest breath level

serial = usb_cdc.data or usb_cdc.console
serial.timeout = 0

ADDR = 0x44
_cmd = bytes((0xFD,))  # measure, high precision
_rx = bytearray(6)
i2c = board.I2C()

try:
    _np = digitalio.DigitalInOut(board.NEOPIXEL)
    _np.direction = digitalio.Direction.OUTPUT
    _grb = bytearray(3)
except Exception:
    _np = None

hum = None  # last humidity; None = no reading / sensor error


def read():
    global hum
    while not i2c.try_lock():
        pass
    try:
        i2c.writeto(ADDR, _cmd)
    finally:
        i2c.unlock()
    time.sleep(0.01)  # ~8.2 ms conversion
    while not i2c.try_lock():
        pass
    try:
        i2c.readfrom_into(ADDR, _rx)
    finally:
        i2c.unlock()
    t = -45.0 + 175.0 * ((_rx[0] << 8) | _rx[1]) / 65535.0
    h = -6.0 + 125.0 * ((_rx[3] << 8) | _rx[4]) / 65535.0
    hum = 0.0 if h < 0 else 100.0 if h > 100 else h
    return t, hum


def _set(c):
    if _np:
        _grb[0], _grb[1], _grb[2] = c[1], c[0], c[2]  # wire order G, R, B
        neopixel_write.neopixel_write(_np, _grb)


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
    if not _np:
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
    return len(toks) == len(spec) and all(_ab(s, t) for s, t in zip(spec, toks))


def _pct(a):
    v = float(a)
    if not 0 <= v <= 100:
        raise ValueError("range 0..100")
    return v


def handle(line):
    global LOW, HIGH, WARN
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
    if q and _is(t, "SYSTem", "ERRor"):
        return '0,"No error"'
    return '-113,"Undefined header"'


gc.collect()
try:
    read()
except Exception:
    pass

buf = b""
nxt = time.monotonic()
while True:
    now = time.monotonic()
    if now >= nxt:
        nxt = now + PERIOD
        try:
            read()
        except Exception:
            hum = None
        gc.collect()
    indicate(now)
    n = serial.in_waiting
    if n:
        buf += serial.read(n)
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
                if r is not None:
                    serial.write((r + "\n").encode())
            except Exception as e:
                serial.write(('-100,"%s"\n' % e).encode())
    time.sleep(0.01)
