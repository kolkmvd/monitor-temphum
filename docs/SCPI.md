# SCPI command reference

The SHT45 Trinkey behaves as a small SCPI instrument over its USB *data* serial
port. Commands are ASCII, terminated with a newline (`\n`). Queries (ending in
`?`) return exactly one newline-terminated line; set/action commands return
nothing.

## Commands

### Measurement

| Command             | Response                     | Notes                              |
|---------------------|------------------------------|------------------------------------|
| `*IDN?`             | `MYOSS,SHT45-Trinkey,0001,1.0` | Identify (vendor,model,serial,fw) |
| `*RST`              | _(none)_                     | Restore default humidity thresholds |
| `*CLS`              | _(none)_                     | Clear status; no-op                |
| `MEAS?`             | `<temp_c>,<humidity_pct>`    | e.g. `22.84,41.15`                 |
| `MEAS:TEMP?`        | `<temp_c>`                   | Temperature in °C                  |
| `MEAS:HUM?`         | `<humidity_pct>`             | Relative humidity in %             |
| `SYST:ERR?`         | `0,"No error"`               | Error queue (always clean here)    |

### Humidity thresholds (status LED)

The onboard NeoPixel reflects the latest humidity in three zones:

| Zone   | Condition                                 | LED                     |
|--------|-------------------------------------------|-------------------------|
| `IN`   | `low + warn ≤ h ≤ high - warn`            | green, slow glow        |
| `WARN` | within `warn` % of `low` or `high`        | amber, faster glow      |
| `OUT`  | `h < low`, `h > high`, or no valid read   | red, blinking           |

Thresholds are set commands (no `?`); they live in RAM and reset to the
settings.toml defaults (`40 / 70 / 5`) on `*RST` or power cycle. When the
exporter runs with `HUM_LOW/HIGH/WARN`, it pushes them on connect.

| Command             | Response              | Notes                         |
|---------------------|-----------------------|-------------------------------|
| `CALC:HUM:LOW 40`   | _(none)_              | Set lower threshold (%), 0..100 |
| `CALC:HUM:LOW?`     | `<low_pct>`           | Query lower threshold         |
| `CALC:HUM:HIGH 70`  | _(none)_              | Set upper threshold (%), 0..100 |
| `CALC:HUM:HIGH?`    | `<high_pct>`          | Query upper threshold         |
| `CALC:HUM:WARN 5`   | _(none)_              | Set warning margin (%), 0..100 |
| `CALC:HUM:WARN?`    | `<warn_pct>`          | Query warning margin          |
| `CALC:HUM:STAT?`    | `IN` \| `WARN` \| `OUT` | Current zone of latest reading |

Unknown headers return `-113,"Undefined header"`. A bad/missing argument or a
measurement exception returns `-100,"<message>"`.

Keywords are case-insensitive. Both long and short forms are accepted
(`MEAS?` / `MEASURE?`, `MEAS:TEMP?` / `MEAS:TEMPERATURE?`,
`CALC:…` / `CALCULATE:…`).

## Manual test

Any serial terminal at any baud (USB CDC ignores baud). Example with Python:

```python
import serial
s = serial.Serial("COM5", 115200, timeout=2)   # the DATA port
s.write(b"*IDN?\n");  print(s.readline())
s.write(b"MEAS?\n");  print(s.readline())       # b'22.84,41.15\n'
```

Or on Linux/macOS:

```bash
printf '*IDN?\n'  > /dev/ttyACM1 ; head -1 /dev/ttyACM1
```

## Notes on the serial port

`boot.py` enables both a console and a data CDC port, so the host sees **two**
serial devices. SCPI runs on the **data** port (usually the second COM /
higher ttyACM number). The console port carries the CircuitPython REPL and must
not be used for SCPI. To collapse to a single clean port, set
`usb_cdc.enable(console=False, data=True)` in `boot.py`.
