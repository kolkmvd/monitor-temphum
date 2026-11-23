"""Prometheus exporter that polls an SHT45 Trinkey over a SCPI serial link.

The Trinkey exposes a USB CDC serial port and answers SCPI queries such as
``MEAS?`` (returns ``<temperature_c>,<humidity_pct>``). This exporter polls the
device on a fixed interval and publishes the readings as Prometheus gauges on an
HTTP ``/metrics`` endpoint.

Configuration is via environment variables (see .env.example):

    SCPI_PORT       serial device                 (default /dev/ttyACM0)
    SCPI_BAUD       baud rate (ignored by USB CDC) (default 115200)
    SCPI_TIMEOUT    read timeout seconds           (default 2.0)
    POLL_INTERVAL   seconds between reads          (default 5.0)
    EXPORTER_PORT   HTTP port for /metrics         (default 9101)
    LOCATION        optional 'location' label value
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

_LABELS = {"location": LOCATION} if LOCATION else {}
_LABELNAMES = list(_LABELS.keys())

temperature = Gauge(
    "sht45_temperature_celsius", "Temperature in degrees Celsius", _LABELNAMES
)
humidity = Gauge(
    "sht45_humidity_percent", "Relative humidity in percent", _LABELNAMES
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


def _m(metric):
    """Apply the configured labels to a metric (or return it bare)."""
    return metric.labels(**_LABELS) if _LABELNAMES else metric


class SCPIDevice:
    """Thread-safe SCPI serial client with lazy (re)connect."""

    def __init__(self, port, baud, timeout):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self._serial = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._serial is not None and self._serial.is_open:
            return
        LOG.info("opening serial port %s @ %d", self.port, self.baud)
        self._serial = serial.Serial(self.port, self.baud, timeout=self.timeout)
        time.sleep(0.2)  # let the CDC link settle
        self._serial.reset_input_buffer()

    def query(self, command):
        with self._lock:
            self._connect()
            self._serial.reset_input_buffer()
            self._serial.write((command + "\n").encode("ascii"))
            line = self._serial.readline().decode("ascii", "replace").strip()
            if not line:
                raise TimeoutError("no response to {!r}".format(command))
            return line

    def close(self):
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self._serial = None


def poll_once(device):
    reply = device.query("MEAS?")
    temp_str, hum_str = reply.split(",")
    t, h = float(temp_str), float(hum_str)
    _m(temperature).set(t)
    _m(humidity).set(h)
    _m(up).set(1)
    _m(last_read).set(time.time())
    LOG.debug("temp=%.2f C humidity=%.2f %%", t, h)


def main():
    start_http_server(EXPORTER_PORT)
    LOG.info("exporter listening on :%d/metrics", EXPORTER_PORT)
    device = SCPIDevice(SCPI_PORT, SCPI_BAUD, SCPI_TIMEOUT)
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
