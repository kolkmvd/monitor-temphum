"""Prometheus exporter that polls an SHT45 Trinkey over a SCPI serial link.

The Trinkey exposes a USB CDC serial port and answers SCPI queries such as
``MEAS?`` (returns ``<temperature_c>,<humidity_pct>``). This exporter polls the
device on a fixed interval and publishes the readings as Prometheus gauges on
an HTTP ``/metrics`` endpoint.

Configuration is via environment variables (see .env.example):

    SCPI_PORT       serial device                 (default /dev/ttyACM0)
    SCPI_BAUD       baud rate (ignored by USB CDC) (default 115200)
    SCPI_TIMEOUT    read timeout seconds           (default 2.0)
    POLL_INTERVAL   seconds between reads          (default 5.0)
    EXPORTER_PORT   HTTP port for /metrics         (default 9101)
    LOCATION        optional 'location' label value
    HUM_LOW         lower humidity limit (%)       (optional)
    HUM_HIGH        upper humidity limit (%)       (optional)
    HUM_WARN        warning margin inside limit (%) (optional)
    BRIGHTNESS      status-LED brightness 0..1     (optional)

When HUM_LOW/HIGH/WARN/BRIGHTNESS are set they are pushed to the Trinkey over
SCPI on every (re)connect, so the status LED follows docker-compose config.
Leave them empty to keep the firmware's own settings.toml defaults.
"""

import logging
import os
import threading
import time

import serial
from prometheus_client import Counter, Gauge, start_http_server

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("scpi_exporter")

SCPI_PORT = os.getenv("SCPI_PORT", "/dev/ttyACM0")
SCPI_BAUD = int(os.getenv("SCPI_BAUD", "115200"))
SCPI_TIMEOUT = float(os.getenv("SCPI_TIMEOUT", "2.0"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "5.0"))
EXPORTER_PORT = int(os.getenv("EXPORTER_PORT", "9101"))
LOCATION = os.getenv("LOCATION", "").strip()


def _opt_float(name):
    """Return an env var as float, or None if unset/empty."""
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else None


HUM_LOW = _opt_float("HUM_LOW")
HUM_HIGH = _opt_float("HUM_HIGH")
HUM_WARN = _opt_float("HUM_WARN")
BRIGHT = _opt_float("BRIGHTNESS")
TEMP_OFFSET = _opt_float("TEMP_OFFSET")
HUM_OFFSET = _opt_float("HUM_OFFSET")

_LABELS = {"location": LOCATION} if LOCATION else {}
_LABELNAMES = list(_LABELS.keys())

temperature = Gauge(
    "sht45_temperature_celsius", "Temperature in degrees Celsius", _LABELNAMES
)
humidity = Gauge(
    "sht45_humidity_percent", "Relative humidity in percent", _LABELNAMES
)
temperature_raw = Gauge(
    "sht45_temperature_raw_celsius",
    "Uncalibrated temperature in degrees Celsius",
    _LABELNAMES,
)
humidity_raw = Gauge(
    "sht45_humidity_raw_percent",
    "Uncalibrated relative humidity in percent",
    _LABELNAMES,
)
up = Gauge("sht45_up", "1 if the last read succeeded, else 0", _LABELNAMES)
last_read = Gauge(
    "sht45_last_read_timestamp_seconds",
    "Unix timestamp of the last successful read",
    _LABELNAMES,
)
read_errors = Counter(
    "sht45_read_errors_total", "Number of failed SCPI reads", _LABELNAMES
)
hum_low_g = Gauge(
    "sht45_humidity_low_percent",
    "Configured lower humidity limit",
    _LABELNAMES,
)
hum_high_g = Gauge(
    "sht45_humidity_high_percent",
    "Configured upper humidity limit",
    _LABELNAMES,
)
hum_warn_g = Gauge(
    "sht45_humidity_warn_percent",
    "Configured warning margin inside each limit",
    _LABELNAMES,
)


def _m(metric):
    """Apply the configured labels to a metric (or return it bare)."""
    return metric.labels(**_LABELS) if _LABELNAMES else metric


class SCPIDevice:
    """Thread-safe SCPI serial client with lazy (re)connect."""

    def __init__(self, port, baud, timeout, on_connect=None):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._on_connect = on_connect
        self._serial = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._serial is not None and self._serial.is_open:
            return
        LOG.info("opening serial port %s @ %d", self.port, self.baud)
        self._serial = serial.Serial(
            self.port, self.baud, timeout=self.timeout
        )
        time.sleep(0.2)  # let the CDC link settle
        self._serial.reset_input_buffer()
        if self._on_connect is not None:
            self._on_connect(self)

    def query(self, command):
        with self._lock:
            self._connect()
            self._serial.reset_input_buffer()
            self._serial.write((command + "\n").encode("ascii"))
            line = self._serial.readline().decode("ascii", "replace").strip()
            if not line:
                raise TimeoutError("no response to {!r}".format(command))
            return line

    def write_cmd(self, command):
        """Send a set command (no response). Assumes a live link."""
        self._serial.write((command + "\n").encode("ascii"))
        time.sleep(0.05)  # let the firmware consume the line

    def close(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self._serial = None


def poll_once(device):
    temp_str, hum_str = device.query("MEAS?").split(",")
    t, h = float(temp_str), float(hum_str)
    _m(temperature).set(t)
    _m(humidity).set(h)
    # Uncalibrated reading from the same sample, for comparison in Grafana.
    rt_str, rh_str = device.query("MEAS:RAW?").split(",")
    _m(temperature_raw).set(float(rt_str))
    _m(humidity_raw).set(float(rh_str))
    _m(up).set(1)
    _m(last_read).set(time.time())
    LOG.debug("temp=%.2f C humidity=%.2f %%", t, h)


def _threshold_commands():
    """SCPI set commands for the configured settings (skipping unset ones)."""
    cmds = []
    if HUM_LOW is not None:
        cmds.append("CALC:HUM:LOW {:.2f}".format(HUM_LOW))
    if HUM_HIGH is not None:
        cmds.append("CALC:HUM:HIGH {:.2f}".format(HUM_HIGH))
    if HUM_WARN is not None:
        cmds.append("CALC:HUM:WARN {:.2f}".format(HUM_WARN))
    if BRIGHT is not None:
        cmds.append("DISP:BRIG {:.2f}".format(BRIGHT))
    if TEMP_OFFSET is not None:
        cmds.append("CAL:TEMP:OFFS {:.2f}".format(TEMP_OFFSET))
    if HUM_OFFSET is not None:
        cmds.append("CAL:HUM:OFFS {:.2f}".format(HUM_OFFSET))
    return cmds


def push_config(device):
    """Push configured thresholds to the Trinkey (runs on every reconnect)."""
    cmds = _threshold_commands()
    for cmd in cmds:
        device.write_cmd(cmd)
    if cmds:
        LOG.info("pushed thresholds: %s", ", ".join(cmds))


def _publish_threshold_gauges():
    if HUM_LOW is not None:
        _m(hum_low_g).set(HUM_LOW)
    if HUM_HIGH is not None:
        _m(hum_high_g).set(HUM_HIGH)
    if HUM_WARN is not None:
        _m(hum_warn_g).set(HUM_WARN)


def main():
    start_http_server(EXPORTER_PORT)
    LOG.info("exporter listening on :%d/metrics", EXPORTER_PORT)
    _publish_threshold_gauges()
    device = SCPIDevice(
        SCPI_PORT, SCPI_BAUD, SCPI_TIMEOUT, on_connect=push_config
    )
    _m(up).set(0)

    while True:
        try:
            poll_once(device)
        except Exception as exc:
            _m(up).set(0)
            _m(read_errors).inc()
            LOG.warning("read failed: %s", exc)
            device.close()  # force a fresh connect next cycle
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
