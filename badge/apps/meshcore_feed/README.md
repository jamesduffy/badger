# MeshCore feed

Shows MeshCore messages and contacts from your own companion node over Bluetooth LE, with a demo mode that needs no radio.

Untested on hardware. Live mode is a prototype built from the documented MeshCore companion protocol, and it may fail at BLE pairing on the badge firmware.

## Usage

Pick **Demo** to browse canned messages and contacts (works in the browser simulator). Pick **Live** to scan for a MeshCore BLE companion node, pair with PIN 123456 (the MeshCore default, edit `BLE_PIN` in `__init__.py` if you changed it), and sync messages and contacts.

- UP/DOWN: scroll
- RIGHT/LEFT: switch between messages and contacts
- SELECT on a direct message: pick a canned reply and send it (live mode sends a real MeshCore message)
- BACK: return to the start screen

Needs a MeshCore companion node running the BLE companion firmware, not the USB-only or Wi-Fi build. No credentials are stored. See `editor/meshcore-feasibility.md` for sources and what is verified.
