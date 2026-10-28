# Overhead

Show aircraft near a place you choose, with where each is coming from and going to, its type, altitude, speed and heading.

## Usage

Needs Wi-Fi. Set `WIFI_SSID` and `WIFI_PASSWORD` in `secrets.py` on the badge. The place defaults to the GitHub Universe 2026 venue (37.8068, -122.4315). To use another, set `MAP_LAT` and `MAP_LON` (decimal degrees, for example `MAP_LAT = 12.3456`). Both must be set and valid, otherwise the default is used. Optionally set `PLANES_RADIUS_NM` (nautical miles, default 15, limited to 3 to 40). Without Wi-Fi after 10 seconds, or with `PLANES_DEMO = 1`, the app runs on demo data with made-up aircraft and airports around the chosen place and uses no network.

Controls:

* Up and Down pick an aircraft.
* Select switches between the list with detail, and the radar.
* Back exits.

The list is sorted by distance and shows callsign, type, altitude and ground speed in knots. Altitude is feet, or a flight level (FL350) from 18,000 ft. The detail panel adds the route, heading, vertical rate, range and bearing from you, and why it was marked arriving or departing. The radar has you in the centre, north up, with rings at a third of the range. Aircraft are arrows pointing along their track and move between updates. The selected one is lime. Arrows are green for arriving, amber for departing, blue for level flight and grey on the ground.

Positions refresh about every 12 seconds. Each request blocks the screen for about a second, so route lookups run one at a time, 4 seconds apart, for the nearest aircraft and the one selected. Routes are cached per callsign. Many aircraft have no known route, and the detail panel then shows "no route".

## How arriving and departing is decided

1. On the ground: GND.
2. If the route is known and the aircraft is within 80 nm of its origin or destination, the nearer one decides: nearer the destination is ARR, nearer the origin is DEP. If it is climbing 800 fpm or more on an "arrival" (or descending that fast on a "departure"), the route is ignored, since the callsign was probably reused on another flight.
3. Otherwise vertical rate decides. Descending or climbing 300 fpm or more below 15,000 ft is ARR or DEP. Above that it is DSC or CLB, because a cruise level change does not say anything about an airport.
4. Anything else is LVL.

Vertical rate is the barometric rate, or the geometric one when that is missing. Routes are looked up by callsign, so they can be wrong or stale. Treat ARR and DEP as a good guess.

## Notes

The data size is about 500 bytes per aircraft. A 25 nm circle near a major airport returned 83 aircraft (42 KB), which is why the default radius is 15 nm and the radius is capped at 40. Only the nearest 40 aircraft are kept. Both services are free and need no key, but adsb.lol says it may require a key in future and asks production users to contact them. The app sends one position request per 12 seconds. adsb.lol returned a rate limit header of `4, 4;w=1, 60;w=60, 500;w=86400`. If that is a daily cap of 500 requests per address, one position request per 12 seconds uses 500 in about 100 minutes. The meaning of that header is not documented, so the app shows the HTTP error on screen if it gets refused.

Everything in `secrets.py` stays in plain text on the badge. Never commit real values.

## Data sources

* Aircraft positions: [adsb.lol](https://api.adsb.lol), open data under the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
* Routes: [adsbdb](https://www.adsbdb.com), a public API for aircraft, airlines and flight routes.
