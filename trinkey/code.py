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
# Thresholds live in RAM and reset to the settings.toml defaults on *RST or
# power cycle; the exporter pushes docker-compose values over SCPI on connect.

import os
import time

import board
import usb_cdc

import adafruit_sht4x

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
GLOW_SPEED = 0.02  # green glow animation step (s)
WARN_SPEED = 0.02  # amber glow animation step (s)
BLINK_SPEED = 0.3  # red on/off step (s)

# Background sensor read interval (s), from settings.toml. Independent of SCPI:
# the LED refreshes on its own. Clamped to 1..60 so it always reads at least
# once a minute, even when nothing is polling over SCPI.
MEASURE_PERIOD = max(1.0, min(_env_float("MEASURE_INTERVAL", 5.0), 60.0))

# Prefer the dedicated data port; fall back to the console if data is disabled.
serial = usb_cdc.data if usb_cdc.data is not None else usb_cdc.console
serial.timeout = 0  # non-blocking reads; we drive timing via in_waiting

# Optional status LED + animations (present on the SHT4x Trinkey).
try:
    import neopixel
    from adafruit_led_animation.animation.blink import Blink
    from adafruit_led_animation.animation.pulse import Pulse

    _pixel = neopixel.NeoPixel(
        board.NEOPIXEL, 1, brightness=0.3, auto_write=False
    )
    _glow = Pulse(
        _pixel, speed=GLOW_SPEED, color=GLOW_COLOR, period=GLOW_PERIOD
    )
    _warn = Pulse(
        _pixel, speed=WARN_SPEED, color=WARN_COLOR, period=WARN_PERIOD
    )
    _alarm = Blink(_pixel, speed=BLINK_SPEED, color=ALARM_COLOR)
except Exception:  # pragma: no cover - hardware/library optional
    _pixel = _glow = _warn = _alarm = None

i2c = board.I2C()
sht = adafruit_sht4x.SHT4x(i2c)
sht.mode = adafruit_sht4x.Mode.NOHEAT_HIGHPRECISION

# Mutable runtime state.
hum_low = DEFAULT_HUM_LOW
hum_high = DEFAULT_HUM_HIGH
hum_warn = DEFAULT_HUM_WARN
_last_temp = None
_last_hum = None  # None = no valid reading yet / sensor error

_buffer = b""


def _read():
    """Read the sensor, cache the result, return (temp_c, humidity_pct)."""
    global _last_temp, _last_hum
    t, h = sht.measurements  # (temperature_c, relative_humidity_pct)
    _last_temp, _last_hum = t, h
    return t, h


def _zone(h):
    """Classify humidity: 'IN' (comfort), 'WARN' (near a limit) or 'OUT'."""
    if h is None or h < hum_low or h > hum_high:
        return "OUT"
    if h < hum_low + hum_warn or h > hum_high - hum_warn:
        return "WARN"
    return "IN"


def _update_indicator():
    """Animate the NeoPixel from the last cached humidity reading."""
    if _pixel is None:
        return
    zone = _zone(_last_hum)
    if zone == "IN":
        _glow.animate()
    elif zone == "WARN":
        _warn.animate()
    else:
        _alarm.animate()


def handle(line):
    """Return the response for one SCPI line, or None for no response."""
    global hum_low, hum_high, hum_warn

    parts = line.split(None, 1)
    header = parts[0].upper()
    arg = parts[1].strip() if len(parts) > 1 else None

    if header in ("*IDN?", "*IDN"):
        return IDN
    if header == "*RST":
        hum_low = DEFAULT_HUM_LOW
        hum_high = DEFAULT_HUM_HIGH
        hum_warn = DEFAULT_HUM_WARN
        return None
    if header == "*CLS":
        return None

    if header in ("MEAS?", "MEASURE?", "READ?"):
        t, h = _read()
        return "{:.2f},{:.2f}".format(t, h)
    if header in ("MEAS:TEMP?", "MEAS:TEMPERATURE?"):
        t, _ = _read()
        return "{:.2f}".format(t)
    if header in ("MEAS:HUM?", "MEAS:HUMIDITY?"):
        _, h = _read()
        return "{:.2f}".format(h)

    if header in ("CALC:HUM:LOW?", "CALCULATE:HUM:LOW?"):
        return "{:.2f}".format(hum_low)
    if header in ("CALC:HUM:HIGH?", "CALCULATE:HUM:HIGH?"):
        return "{:.2f}".format(hum_high)
    if header in ("CALC:HUM:WARN?", "CALCULATE:HUM:WARN?"):
        return "{:.2f}".format(hum_warn)
    if header in ("CALC:HUM:STAT?", "CALC:HUM:STATE?"):
        return _zone(_last_hum)

    if header in ("CALC:HUM:LOW", "CALCULATE:HUM:LOW"):
        hum_low = _parse_pct(arg)
        return None
    if header in ("CALC:HUM:HIGH", "CALCULATE:HUM:HIGH"):
        hum_high = _parse_pct(arg)
        return None
    if header in ("CALC:HUM:WARN", "CALCULATE:HUM:WARN"):
        hum_warn = _parse_pct(arg)
        return None

    if header in ("SYST:ERR?", "SYSTEM:ERROR?"):
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

    _update_indicator()

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
