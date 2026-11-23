# monitor-temphum

Temperature & humidity monitoring with **Grafana/Prometheus**, measured by an
**Adafruit SHT45 Trinkey** that speaks **SCPI** over USB serial.

```
 SHT45 Trinkey ──SCPI/serial──> scpi_exporter (host) ──/metrics──> Prometheus ──> Grafana
```

## Components

| Path                          | What it is                                               |
|-------------------------------|----------------------------------------------------------|
| `trinkey/boot.py`             | Disables USB-MIDI/HID; enables a clean serial port       |
| `trinkey/code.py`             | CircuitPython SCPI firmware for the SHT45                 |
| `exporter/scpi_exporter.py`   | Polls SCPI, exposes Prometheus metrics on `:9101`        |
| `prometheus/prometheus.yml`   | Scrape config                                            |
| `grafana/`                    | Auto-provisioned datasource + dashboard                  |
| `docker-compose.yml`          | Prometheus + Grafana (exporter runs on the host)         |
| `docs/SCPI.md`                | SCPI command reference                                   |

## 1. Flash the Trinkey

1. Install **CircuitPython** on the SHT45 Trinkey (drag the `.uf2`, or use the
   web installer at circuitpython.org).
2. Install the libraries with `circup` (resolves dependencies automatically):
   ```bash
   cd trinkey && ./install-libs.sh      # Trinkey plugged in, CIRCUITPY mounted
   ```
   This installs `adafruit_sht4x`, `neopixel` and `adafruit_led_animation` into
   `CIRCUITPY/lib/`. (Manual alternative: copy those from the matching
   [Adafruit CircuitPython Bundle](https://circuitpython.org/libraries).)
3. Copy `trinkey/boot.py`, `trinkey/code.py` and `trinkey/settings.toml` to the
   root of `CIRCUITPY`. Edit `settings.toml` to set the default humidity
   thresholds (`HUM_LOW_DEFAULT` / `HUM_HIGH_DEFAULT`).
4. **Eject and replug** the Trinkey so `boot.py` re-runs. It now enumerates as
   two serial ports; SCPI lives on the **data** port (see `docs/SCPI.md`).

Quick check (replace `COM5` with the data port):

```bash
python -c "import serial;s=serial.Serial('COM5',115200,timeout=2);s.write(b'MEAS?\n');print(s.readline())"
```

## 2. Point the exporter at a stable serial device (Linux)

`/dev/ttyACMx` depends on plug order, so use the by-id path (the `-if02` entry
is the data/SCPI port):

```bash
ls -l /dev/serial/by-id/      # usb-Adafruit_..._Trinkey_<serial>-if02
```

Put that path in the `exporter` service's `devices:` mapping in
`docker-compose.yml`. Inside the container it is exposed as `/dev/ttyACM0`
(`SCPI_PORT`). Optionally set `LOCATION` there to label the sensor.

> **Windows/macOS host?** Docker Desktop can't pass USB serial through. Run the
> exporter natively instead (`cd exporter && pip install -r requirements.txt &&
> SCPI_PORT=COM5 python scpi_exporter.py`), delete the `exporter` service, and
> switch the Prometheus target to `host.docker.internal:9101`.

## 3. Start everything

```bash
docker compose up -d           # exporter + Prometheus + Grafana
```

- Exporter: <http://localhost:9101/metrics> shows `sht45_temperature_celsius` etc.
- Prometheus: <http://localhost:9090> (check *Status → Targets* is `UP`)
- Grafana: <http://localhost:3000> (admin / admin) → dashboard
  **Temperature & Humidity (SHT45)** is pre-loaded.

## Metrics

| Metric                                | Description                        |
|---------------------------------------|------------------------------------|
| `sht45_temperature_celsius`           | Temperature (°C)                   |
| `sht45_humidity_percent`              | Relative humidity (%)              |
| `sht45_up`                            | 1 = last read ok, 0 = failed       |
| `sht45_last_read_timestamp_seconds`   | Unix time of last good read        |
| `sht45_read_errors_total`             | Cumulative read failures           |
| `sht45_humidity_low_percent`          | Configured lower limit (if set)    |
| `sht45_humidity_high_percent`         | Configured upper limit (if set)    |
| `sht45_humidity_warn_percent`         | Configured warning margin (if set) |

An optional `LOCATION` env var adds a `location` label to every metric, so one
Prometheus can scrape several sensors.

## Status LED & humidity limits

The Trinkey's NeoPixel shows the humidity zone at a glance: **green slow glow**
inside the band, **amber faster glow** within `HUM_WARN` % of a limit, and
**red blink** when out of range or the sensor can't be read.

Set `HUM_LOW` / `HUM_HIGH` / `HUM_WARN` on the `exporter` service in
`docker-compose.yml`; the exporter pushes them to the Trinkey over SCPI on every
(re)connect. Without them, the firmware keeps the defaults from its own
`settings.toml`. The limits are also exported as metrics so the dashboard can
draw threshold bands.

The LED is driven entirely by the firmware, independent of Prometheus or the
exporter: the Trinkey reads the sensor on its own every `MEASURE_INTERVAL`
seconds (`settings.toml`, clamped to 1..60), so the colour stays live even when
nothing is polling over SCPI.
