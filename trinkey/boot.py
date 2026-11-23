# boot.py -- runs once at power-up, before code.py.
#
# Goal: make the SHT45 Trinkey enumerate as a clean USB serial (SCPI) device.
# CircuitPython enables USB-MIDI and USB-HID by default; we disable both so the
# host only sees CDC serial ports and nothing grabs the device as a MIDI/HID
# endpoint.

import usb_cdc
import usb_hid
import usb_midi

# Drop the interfaces we do not want on the wire.
usb_midi.disable()
usb_hid.disable()

# Keep the console (REPL, for debugging) and add a dedicated *data* serial port.
# SCPI traffic runs over the data port so it never collides with REPL output.
# On the host this shows up as a SECOND COM port / ttyACM device -- point the
# exporter (SCPI_PORT) at that one.
#
# Want a single clean port instead? Use: usb_cdc.enable(console=False, data=True)
usb_cdc.enable(console=True, data=True)
