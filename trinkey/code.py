# code.py -- SCPI instrument firmware for the Adafruit SHT45 Trinkey.
#
# Speaks a small SCPI dialect over the USB data serial port (see boot.py).
# Commands are newline-terminated. Queries (ending in '?') return one line;
# set commands (no '?') return nothing.
#
#   *IDN?              -> MYOSS,SHT45-Trinkey,0001,1.0
#   *RST               -> restore default humidity thresholds (no response)
#   *CLS               -> (no response)
#   MEAS?              -> "<temp_c>,<humidity_pct>"   e.g. "22.84,41.15"
#   MEAS:TEMP?         -> "<temp_c>"
#   MEAS:HUM?          -> "<humidity_pct>"
#   CALC:HUM:LOW 40    -> set lower humidity threshold (%)
#   CALC:HUM:LOW?      -> "<low_pct>"
#   CALC:HUM:HIGH 70   -> set upper humidity threshold (%)
#   CALC:HUM:HIGH?     -> "<high_pct>"
#   CALC:HUM:WARN 5    -> set warning margin (%) just inside each limit
#   CALC:HUM:WARN?     -> "<warn_pct>"
#   CALC:HUM:STAT?     -> "IN" | "WARN" | "OUT"
#   SYST:ERR?          -> 0,"No error"
#
# Status LED (onboard NeoPixel), driven by the last humidity reading:
#   IN    comfort zone                     -> green, slow breathing glow
#   WARN  within 'warn' % of a limit       -> amber, faster glow
#   OUT   below low / above high / no read -> red blink
#
# RAM note: the SHT4x Trinkey is a SAMD21 with only ~32 kB RAM, so this talks to
# the sensor directly over I2C and drives the pixel with neopixel_write -- no
# adafruit_sht4x / neopixel libraries, which together would not fit.

import gc
import math
import os
import time

import board
import digitalio
import neopixel_write
import usb_cdc

IDN = "MYOSS,SHT45-Trinkey,0001,1.0"


def _env_float(name, fallback):
    # os.getenv() returns str for settings.toml ints since CircuitPython
    # 10.2.0, so parse defensively instead of assuming a numeric type.
    value = os.getenv(name)
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# Power-on defaults from settings.toml; overridable at runtime over SCPI
# (the exporter pushes HUM_LOW/HIGH/WARN from docker-compose on connect).
DEFAULT_HUM_LOW = _env_float("HUM_LOW_DEFAULT", 40.0)
DEFAULT_HUM_HIGH = _env_float("HUM_HIGH_DEFAULT", 70.0)
DEFAULT_HUM_WARN = _env_float("HUM_WARN_DEFAULT", 5.0)  # warning margin (%)

GLOW_COLOR = (0, 80, 0)  # comfort zone: green
WARN_COLOR = (90, 60, 0)  # warning zone (near a limit): amber/yellow
ALARM_COLOR = (40, 0, 0)  # out of range: red
GLOW_PERIOD = 3.0  # green breath length (s)
WARN_PERIOD = 0.8  # amber breath length (s) -- faster = more urgent
BLINK_PERIOD = 0.6  # red on/off cycle length (s)
GLOW_FLOOR = 0.15  # dimmest point of a breath (0..1)

# Background sensor read interval (s), from settings.toml. Independent of SCPI:
# the LED refreshes on its own. Clamped to 1..60 so it always reads at least
# once a minute, even when nothing is polling over SCPI.
MEASURE_PERIOD = max(1.0, min(_env_float("MEASURE_INTERVAL", 5.0), 60.0))

# Prefer the dedicated data port; fall back to the console if data is disabled.
serial = usb_cdc.data if usb_cdc.data is not None else usb_cdc.console
serial.timeout = 0  # non-blocking reads; we drive timing via in_waiting

# SHT4x over raw I2C (no driver library). 0xFD = measure, high precision.
SHT4X_ADDR = 0x44
_cmd = bytes((0xFD,))
_rx = bytearray(6)
i2c = board.I2C()

# Status LED via neopixel_write (no neopixel library).
try:
    _np = digitalio.DigitalInOut(board.NEOPIXEL)
    _np.direction = digitalio.Direction.OUTPUT
    _grb = bytearray(3)
except Exception:  # pragma: no cover - hardware optional
    _np = None

# Mutable runtime state.
hum_low = DEFAULT_HUM_LOW
hum_high = DEFAULT_HUM_HIGH
hum_warn = DEFAULT_HUM_WARN
_last_temp = None
_last_hum = None  # None = no valid reading yet / sensor error

_buffer = b""


def _read():
    """Read the SHT4x over I2C, cache and return (temp_c, humidity_pct)."""
    global _last_temp, _last_hum
    while not i2c.try_lock():
        pass
    try:
        i2c.writeto(SHT4X_ADDR, _cmd)
    finally:
        i2c.unlock()
    time.sleep(0.01)  # high-precision conversion takes ~8.2 ms
    while not i2c.try_lock():
        pass
    try:
        i2c.readfrom_into(SHT4X_ADDR, _rx)
    finally:
        i2c.unlock()
    t_ticks = (_rx[0] << 8) | _rx[1]
    rh_ticks = (_rx[3] << 8) | _rx[4]
    t = -45.0 + 175.0 * t_ticks / 65535.0
    rh = -6.0 + 125.0 * rh_ticks / 65535.0
    if rh < 0.0:
        rh = 0.0
    elif rh > 100.0:
        rh = 100.0
    _last_temp, _last_hum = t, rh
    return t, rh


def _pixel_set(color):
    if _np is None:
        return
    _grb[0] = color[1]  # NeoPixel wire order is G, R, B
    _grb[1] = color[0]
    _grb[2] = color[2]
    neopixel_write.neopixel_write(_np, _grb)


def _breathe(now, color, period):
    """Pulse `color` smoothly via a sine on the brightness level."""
    phase = (math.sin(now * (2 * math.pi / period)) + 1) / 2
    level = GLOW_FLOOR + (1 - GLOW_FLOOR) * phase
    _pixel_set((
        int(color[0] * level),
        int(color[1] * level),
        int(color[2] * level),
    ))


def _zone(h):
    """Classify humidity: 'IN' (comfort), 'WARN' (near a limit) or 'OUT'."""
    if h is None or h < hum_low or h > hum_high:
        return "OUT"
    if h < hum_low + hum_warn or h > hum_high - hum_warn:
        return "WARN"
    return "IN"


def _update_indicator(now):
    """Drive the NeoPixel from the last cached humidity reading."""
    if _np is None:
        return
    zone = _zone(_last_hum)
    if zone == "IN":
        _breathe(now, GLOW_COLOR, GLOW_PERIOD)
    elif zone == "WARN":
        _breathe(now, WARN_COLOR, WARN_PERIOD)
    else:
        on = (now % BLINK_PERIOD) < (BLINK_PERIOD / 2)
        _pixel_set(ALARM_COLOR if on else (0, 0, 0))


def _kw_match(spec, token):
    """True if `token` is a valid SCPI abbreviation of keyword `spec`.

    `spec` is written with the mandatory short form in UPPERCASE and the
    optional long-form tail in lowercase, e.g. "MEASure" (short form "MEAS").
    A token matches when it is a prefix of the full keyword and at least as
    long as the short form -- so "MEAS", "MEASU".."MEASURE" all match.
    """
    short = "".join(c for c in spec if c.isupper())
    full = spec.upper()
    return full.startswith(token) and len(token) >= len(short)


def _match(tokens, *spec):
    """True if the colon-split header `tokens` matches keyword specs `spec`."""
    return len(tokens) == len(spec) and all(
        _kw_match(s, t) for s, t in zip(spec, tokens)
    )


def handle(line):
    """Return the response for one SCPI line, or None for no response."""
    global hum_low, hum_high, hum_warn

    parts = line.split(None, 1)
    head = parts[0].upper()
    arg = parts[1].strip() if len(parts) > 1 else None

    query = head.endswith("?")
    if query:
        head = head[:-1]
    tokens = head.split(":")

    # IEEE 488.2 common commands (no colon hierarchy).
    if head == "*IDN":
        return IDN
    if head == "*RST":
        hum_low = DEFAULT_HUM_LOW
        hum_high = DEFAULT_HUM_HIGH
        hum_warn = DEFAULT_HUM_WARN
        return None
    if head == "*CLS":
        return None

    # MEASure subsystem (queries only).
    if query and (_match(tokens, "MEASure") or _match(tokens, "READ")):
        t, h = _read()
        return "{:.2f},{:.2f}".format(t, h)
    if query and _match(tokens, "MEASure", "TEMPerature"):
        t, _ = _read()
        return "{:.2f}".format(t)
    if query and _match(tokens, "MEASure", "HUMidity"):
        _, h = _read()
        return "{:.2f}".format(h)

    # CALCulate:HUMidity thresholds (set or query share one branch).
    if _match(tokens, "CALCulate", "HUMidity", "LOW"):
        if query:
            return "{:.2f}".format(hum_low)
        hum_low = _parse_pct(arg)
        return None
    if _match(tokens, "CALCulate", "HUMidity", "HIGH"):
        if query:
            return "{:.2f}".format(hum_high)
        hum_high = _parse_pct(arg)
        return None
    if _match(tokens, "CALCulate", "HUMidity", "WARN"):
        if query:
            return "{:.2f}".format(hum_warn)
        hum_warn = _parse_pct(arg)
        return None
    if query and _match(tokens, "CALCulate", "HUMidity", "STATe"):
        return _zone(_last_hum)

    # SYSTem subsystem.
    if query and _match(tokens, "SYSTem", "ERRor"):
        return '0,"No error"'

    return '-113,"Undefined header"'


def _parse_pct(arg):
    """Parse a 0..100 percentage argument; raise ValueError otherwise."""
    if arg is None:
        raise ValueError("missing value")
    value = float(arg)
    if not 0.0 <= value <= 100.0:
        raise ValueError("out of range 0..100")
    return value


# Prime the cache once so the LED reflects reality immediately.
gc.collect()
try:
    _read()
except Exception:
    pass

_next_measure = time.monotonic()

while True:
    now = time.monotonic()

    # Background sensor read for the status LED, independent of SCPI traffic.
    if now >= _next_measure:
        _next_measure = now + MEASURE_PERIOD
        try:
            _read()
        except Exception:
            _last_hum = None  # flag sensor error -> red alarm
        gc.collect()

    _update_indicator(now)

    waiting = serial.in_waiting
    if waiting:
        _buffer += serial.read(waiting)
        while b"\n" in _buffer:
            raw, _buffer = _buffer.split(b"\n", 1)
            try:
                command = raw.decode("utf-8").strip()
            except Exception:
                command = ""
            if not command:
                continue
            try:
                response = handle(command)
                if response is not None:
                    serial.write((response + "\n").encode("utf-8"))
            except Exception as exc:  # report, keep running
                serial.write(('-100,"{}"\n'.format(exc)).encode("utf-8"))

    time.sleep(0.01)
