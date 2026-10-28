# APRS map

Live map of APRS stations near a place you choose, with fading movement trails.

## Usage

Needs Wi-Fi. The app connects to the APRS-IS network (`rotate.aprs2.net:14580`) over a TCP socket and asks the server for stations within a radius of your location. It only listens and never transmits. No aprs.fi account or API key is used, because the aprs.fi API cannot search by area.

Set these in `secrets.py` on the badge:

- `WIFI_SSID`, `WIFI_PASSWORD`: as for any network app.
- `MAP_LAT`, `MAP_LON`: map centre in signed decimal degrees (north and east positive, for example `MAP_LAT = 12.3456`, `MAP_LON = -98.7654`). There is no built-in location. If unset the app runs a demo around 0,0 with a banner.
- `MAP_RADIUS_KM`: optional, default 25, range 1 to 500.
- `APRS_CALLSIGN`: optional, your callsign for the login line. Defaults to `N0CALL`.
- `APRS_PASSCODE`: optional, defaults to `-1` (receive only). Leave it alone, the app does not send anything.
- `APRS_DEMO`: optional, set to `1` to force demo data.

Controls:

- Select: cycle zoom (full radius, then 1/2, 1/4, 1/10).
- Up / Down: select the previous or next station, nearest first. When zoomed in the map centres on the selected station and the footer shows speed and course.
- Left: clear the selection and centre on your location.
- Right: switch between live and demo (only when a location is set).
- Back: exit.

The header shows the station count and status. The nearest four stations are labelled. Trails keep the last 10 positions per station, up to 40 stations are held, and a station is dropped after 30 minutes of silence. Stations not heard for 10 minutes are drawn grey.

Decoded packets: uncompressed and compressed position reports (with or without timestamp), objects, and Mic-E. Messages, weather-only packets, items and status reports are ignored.

Demo mode runs canned stations through the same parser so you can try the controls without a network. It also starts by itself if Wi-Fi is not up after 10 seconds, or in the browser simulator, which has no sockets.

The first connect blocks the screen for up to a few seconds. After that reads are non-blocking. Never commit real values in `secrets.py`.
