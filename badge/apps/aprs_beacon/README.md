# APRS beacon

Send your position to the APRS-IS network as your own callsign, once or on a timer, from the badge.

## Usage

For licensed amateur radio operators only. The badge has no radio and no GPS, so this sends one fixed position over Wi-Fi to APRS-IS (the internet side of APRS). It is not an RF transmission, but it does publish a packet under your callsign that anyone can see on aprs.fi. You are responsible for it being correct.

Set these in `secrets.py` on the badge:

- `WIFI_SSID`, `WIFI_PASSWORD`: as for any network app.
- `APRS_CALLSIGN`: your own callsign, with an optional SSID, for example `N0XYZ-7`. Placeholders such as `N0CALL` and `BADGE` are refused.
- `APRS_PASSCODE`: your APRS-IS passcode, a number from 0 to 32767. `-1` is receive only and is refused. The app does not work it out for you. The server answers "unverified" to a wrong one and the app then sends nothing.
- `MAP_LAT`, `MAP_LON`: optional position in signed decimal degrees. Defaults to the GitHub Universe 2026 venue (37.8068, -122.4315). If you point these at your home, that is what gets published.
- `BEACON_COMMENT`: optional, up to 43 plain characters. Defaults to "GitHub Universe 2026 badge".
- `BEACON_INTERVAL_MIN`: optional, minutes between automatic beacons. Default 30, limited to 10 to 120.
- `APRS_DRYRUN`: optional, set to `1` to build the packet and show it without opening a connection.

Controls:

- Select: send one beacon. A screen first shows your callsign, position and comment, and Select again sends it.
- Up: turn automatic beaconing on (asks for the same confirmation, then sends straight away and repeats every interval) or off.
- Back: cancel a confirmation, or exit.

The app logs in, waits for the server to say your login is "verified", sends one position report, and closes the connection. It never retries by itself after a failure. Manual beacons are limited to one every 10 minutes. Automatic beaconing is off every time the app starts. In the browser simulator, which has no sockets, it runs as a dry run.

The packet is `CALL>APZUNV,TCPIP*:!DDMM.mmN/DDDMM.mmW[comment`, a position without timestamp using the human symbol. `APZ` is the experimental destination range, the app has no registered one. Your passcode is only ever put in the login line and is never shown on screen.
