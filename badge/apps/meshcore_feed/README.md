# MeshCore feed

Shows MeshCore messages and contacts from your own companion node over Bluetooth LE, with a demo mode that needs no radio.

Demo mode works (it runs in the simulator). Live mode has not run on a badge and most likely stops with "this firmware cannot pair", because the node requires PIN pairing and the badge BLE stack probably has no pairing support. See `editor/meshcore-feasibility.md`.

## Usage

Pick **Demo** to browse canned messages and contacts. Pick **Live** to scan for a MeshCore BLE companion node, pair with PIN 123456 (the MeshCore default, edit `BLE_PIN` in `__init__.py` if you changed it), and sync messages and contacts. Live shows an error on the start screen if it cannot pair, scan, or stay connected, and you can pick it again to retry.

- UP/DOWN: scroll
- RIGHT/LEFT: switch between messages and contacts
- SELECT on a direct message: pick a canned reply, then SELECT again on a confirm screen that shows the recipient and text. Only that second SELECT sends. BACK cancels at any step. Demo mode never sends.
- BACK: return to the start screen

Needs a MeshCore companion node running the BLE companion firmware, not the USB-only or Wi-Fi build. No credentials are stored.
