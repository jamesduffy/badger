"""
Overhead: aircraft near a place you choose, with where each is coming from and
going to.

Positions come from adsb.lol (v2 point endpoint) and routes from adsbdb, both
free and keyless. Wi-Fi comes from secrets.py. The place defaults to the GitHub
Universe 2026 venue; set MAP_LAT and MAP_LON to use another. With no Wi-Fi, or
PLANES_DEMO set, it runs on demo data.

Controls:
* Up and down = Pick an aircraft
* Select = Switch between list with detail and the radar
* Left and right = Radar range, wider or closer
* Back = Exit
"""

import gc
import math

import requests

try:
    import secrets
except ImportError:
    secrets = None

try:
    import wifi
except ImportError:
    wifi = None

POSITIONS = "https://api.adsb.lol/v2/point/%.4f/%.4f/%d"
ROUTES = "https://api.adsbdb.com/v0/callsign/"
HEADERS = {"User-Agent": "GitHub Universe Badge 2026 overhead app"}

# adsb.lol sent the header `4, 4;w=1, 60;w=60, 500;w=86400`, read by third
# party clients as 500 requests a day per IP address. Conference Wi-Fi shares
# addresses, so positions are fetched rarely and planes are moved along their
# track in between. 120 s is 240 requests over 8 hours.
REFRESH_MS = 120_000        # between position fetches
DEMO_REFRESH_MS = 5_000
ROUTE_GAP_MS = 4_000        # between route lookups, each blocks about a second
RETRY_MS = 30_000
RATE_PAUSE_MS = 600_000     # after adsb.lol answers 429
ROUTE_PAUSE_MS = 60_000     # after a route lookup is refused or fails
TIMEOUT = 8
EXTRAPOLATE_S = 180.0       # radar stops moving planes this long after a fetch

# The point endpoint sends about 500 bytes per aircraft, so a busy 25 nm
# circle was 42 KB. The default radius is smaller, and only the nearest
# MAX_PLANES are kept after parsing.
DEFAULT_RADIUS_NM = 15
MIN_RADIUS_NM = 3
MAX_RADIUS_NM = 40
MAX_PLANES = 40
ROUTE_TOP = 12              # routes are only looked up for the nearest few
MAX_ROUTES = 60             # cached callsigns, oldest dropped first

EARTH_NM = 3440.065
OVERHEAD_NM = 2.0           # an aircraft this close gets the marker in the list

# Radar range as a share of the fetch radius, widest first
VIEWS = (1.0, 0.5, 0.25)

# Arriving or departing, see classify()
NEAR_AIRPORT_NM = 80
CLIMB_FPM = 300
STRONG_FPM = 800
LOCAL_ALT_FT = 15000

badge.mode(HIRES | VSYNC)
screen.font = font.sins
screen.antialias = image.X2

BG = color.rgb(13, 17, 23)
WHITE = color.rgb(235, 245, 255)
DIM = color.rgb(139, 148, 158)
GRID = color.rgb(40, 52, 62)
GREEN = color.rgb(46, 200, 87)
AMBER = color.rgb(240, 170, 40)
RED = color.rgb(248, 81, 73)
BLUE = color.rgb(110, 170, 255)
LIME = color.rgb(211, 250, 55)
BAR_BG = color.rgb(0, 0, 0, 170)

KIND_PEN = {
    "ARR": GREEN, "DEP": AMBER, "DSC": GREEN, "CLB": AMBER,
    "GND": DIM, "LVL": BLUE,
}

W, H = 320, 240
BAR = 16
LIST_W = 176
ROW_H = 16


def number(name):
    """A number from secrets.py, or None when unset or not a number."""
    value = getattr(secrets, name, None)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# GitHub Universe 2026 venue, used unless MAP_LAT and MAP_LON are both set
DEFAULT_LAT = 37.8068139014702
DEFAULT_LON = -122.43150989195377

LAT = number("MAP_LAT")
LON = number("MAP_LON")
if LAT is not None and not -90 <= LAT <= 90:
    LAT = None
if LON is not None and not -180 <= LON <= 180:
    LON = None
if LAT is None or LON is None:
    LAT, LON = DEFAULT_LAT, DEFAULT_LON
DEMO = bool(getattr(secrets, "PLANES_DEMO", ""))
demo_reason = "demo data (PLANES_DEMO set)"
WIFI_WAIT_MS = 10_000       # no wifi after this long: fall back to demo
wifi_wait_from = None

RADIUS = number("PLANES_RADIUS_NM")
RADIUS = DEFAULT_RADIUS_NM if RADIUS is None else int(RADIUS)
RADIUS = max(MIN_RADIUS_NM, min(MAX_RADIUS_NM, RADIUS))


# ---- maths and parsing, kept free of drawing so it can be tested on a host


def distance_nm(lat1, lon1, lat2, lon2):
    """Great circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, 0 to 360, 0 is north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass(deg):
    return COMPASS[int((deg % 360) / 45.0 + 0.5) % 8]


def alt_text(alt, ground):
    if ground:
        return "GND"
    if alt is None:
        return "--"
    if alt >= 18000:
        return "FL%03d" % ((alt + 50) // 100)
    return "%d" % alt


TYPE_NAMES = {
    "A19N": "A319neo", "A20N": "A320neo", "A21N": "A321neo",
    "A319": "A319", "A320": "A320", "A321": "A321",
    "A332": "A330-200", "A333": "A330-300", "A339": "A330-900",
    "A359": "A350-900", "A35K": "A350-1000", "A388": "A380",
    "B712": "717", "B737": "737-700", "B738": "737-800", "B739": "737-900",
    "B38M": "737 MAX 8", "B39M": "737 MAX 9", "B744": "747-400",
    "B748": "747-8", "B752": "757-200", "B763": "767-300", "B772": "777-200",
    "B77W": "777-300ER", "B788": "787-8", "B789": "787-9", "B78X": "787-10",
    "BCS1": "A220-100", "BCS3": "A220-300", "E170": "E170", "E75L": "E175",
    "E75S": "E175", "E190": "E190", "CRJ7": "CRJ-700", "CRJ9": "CRJ-900",
    "DH8D": "Dash 8 Q400", "AT76": "ATR 72", "C172": "Cessna 172",
    "C182": "Cessna 182", "SR22": "Cirrus SR22", "PA28": "Piper PA-28",
    "P28A": "Piper PA-28", "BE20": "King Air 200", "R44": "Robinson R44",
    "B06": "Bell 206", "EC35": "EC135", "H60": "Black Hawk", "GLEX": "Global Exp.",
    "GLF5": "Gulfstream V", "CL60": "Challenger", "C25A": "Citation CJ2",
}


def type_name(code):
    return TYPE_NAMES.get(code, code or "?")


def is_flight(callsign):
    """True for an airline style callsign (UAL928), the only kind adsbdb knows."""
    return len(callsign) >= 4 and callsign[:3].isalpha() and callsign[3].isdigit()


def closing_kt(p):
    """Knots towards the centre, negative when moving away, None if unknown."""
    if p["ground"] or p["gs"] is None or p["trk"] is None or p["dist"] < 0.05:
        return None
    return -(p["e"] * p["ve"] + p["n"] * p["vn"]) / p["dist"] * 3600.0


def airport(raw):
    """(code, lat, lon, city) from an adsbdb airport, or None."""
    if not raw:
        return None
    code = raw.get("iata_code") or raw.get("icao_code")
    if not code:
        return None
    return (code, raw.get("latitude"), raw.get("longitude"), raw.get("municipality"))


def parse_route(data):
    """{"o": airport, "d": airport} from an adsbdb callsign reply, or None.

    An unknown callsign comes back as {"response": "unknown callsign"}.
    """
    try:
        found = data["response"]["flightroute"]
    except (KeyError, TypeError):
        return None
    origin = airport(found.get("origin"))
    dest = airport(found.get("destination"))
    if origin is None and dest is None:
        return None
    return {"o": origin, "d": dest}


def parse_aircraft(a, lat0, lon0):
    """One adsb.lol "ac" record as a small dict, or None with no position.

    alt_baro is feet, or the string "ground". gs is knots, track is degrees,
    baro_rate is feet per minute. Many are missing on some aircraft, so
    heading falls back to true_heading and mag_heading, and vertical rate to
    geom_rate. `flight` is the callsign, padded with spaces.
    """
    lat, lon = a.get("lat"), a.get("lon")
    if lat is None or lon is None:
        return None
    alt = a.get("alt_baro")
    ground = alt == "ground"
    if not isinstance(alt, (int, float)):
        alt = None
    trk = a.get("track")
    if trk is None:
        trk = a.get("true_heading")
    if trk is None:
        trk = a.get("mag_heading")
    vr = a.get("baro_rate")
    if vr is None:
        vr = a.get("geom_rate")
    gs = a.get("gs")
    callsign = (a.get("flight") or "").strip()
    reg = a.get("r") or ""
    dist = distance_nm(lat0, lon0, lat, lon)
    brg = bearing_deg(lat0, lon0, lat, lon)
    rad = math.radians(brg)
    p = {
        "id": a.get("hex") or callsign,
        "call": callsign or reg or a.get("hex") or "?",
        "callsign": callsign,
        "reg": reg,
        "type": a.get("t") or "",
        "alt": alt,
        "ground": ground,
        "gs": gs,
        "trk": trk,
        "vr": vr,
        "mil": isinstance(a.get("dbFlags"), int) and bool(a["dbFlags"] & 1),
        "lat": lat,
        "lon": lon,
        "dist": dist,
        "brg": brg,
        "e": dist * math.sin(rad),
        "n": dist * math.cos(rad),
        "ve": 0.0,
        "vn": 0.0,
    }
    if gs is not None and trk is not None:
        t = math.radians(trk)
        p["ve"] = gs / 3600.0 * math.sin(t)
        p["vn"] = gs / 3600.0 * math.cos(t)
    return p


def route_hint(lat, lon, route):
    """"ARR" or "DEP" from the route alone, or None when it cannot say.

    The aircraft is arriving if the route's destination is the nearer end,
    departing if the origin is. Past NEAR_AIRPORT_NM from both it is on the way
    somewhere, so no hint. Equal distances (a circuit from one field) give none.
    """
    if not route:
        return None
    ends = []
    for tag, key in (("DEP", "o"), ("ARR", "d")):
        ap = route.get(key)
        if ap and ap[1] is not None and ap[2] is not None:
            ends.append((distance_nm(lat, lon, ap[1], ap[2]), tag))
    if not ends:
        return None
    ends.sort()
    near, tag = ends[0]
    if near > NEAR_AIRPORT_NM:
        return None
    if len(ends) == 2 and abs(ends[1][0] - near) < 1.0:
        return None
    return tag


def classify(p, route):
    """(kind, why). kind is GND, ARR, DEP, DSC, CLB or LVL.

    1. On the ground: GND.
    2. A route that places the aircraft near its origin or destination gives
       ARR or DEP. If the vertical rate strongly disagrees (climbing 800+ fpm
       on an "arrival"), the route is probably for another day's flight with
       the same callsign, so it is ignored.
    3. Without that, vertical rate decides: 300+ fpm down or up below
       LOCAL_ALT_FT is ARR or DEP. Higher up that is only DSC or CLB, since a
       cruise level change says nothing about the airport.
    4. Anything else is LVL.
    """
    if p["ground"]:
        return "GND", "on ground"
    vr = p["vr"]
    hint = route_hint(p["lat"], p["lon"], route)
    if hint == "ARR" and vr is not None and vr >= STRONG_FPM:
        hint = None
    elif hint == "DEP" and vr is not None and vr <= -STRONG_FPM:
        hint = None
    if hint:
        return hint, "route"
    if vr is None:
        return "LVL", "no vertical rate"
    low = p["alt"] is not None and p["alt"] < LOCAL_ALT_FT
    if vr <= -CLIMB_FPM:
        return ("ARR" if low else "DSC"), "vertical rate"
    if vr >= CLIMB_FPM:
        return ("DEP" if low else "CLB"), "vertical rate"
    return "LVL", "vertical rate"


# ---- demo data. Offsets are nautical miles east and north of the centre, and
# the airports are made up, so the demo says nothing about any real place.

def _ap(code, e, n, city):
    return (code, n / 60.0, e / 60.0, city)


DEMO_ROUTES = {
    "DMO101": {"o": _ap("BBB", 900, 300, "Faraway"), "d": _ap("AAA", -7, 3, "Demoville")},
    "DMO202": {"o": _ap("AAA", -7, 3, "Demoville"), "d": _ap("CCC", 400, -500, "Sampleton")},
    "DMO303": {"o": _ap("DDD", -1500, 200, "Eastbridge"), "d": _ap("CCC", 1200, 600, "Sampleton")},
}

# call, type, reg, alt, gs, track, vertical rate, east, north
DEMO_PLANES = (
    ("DMO101", "A320", "N-DM101", 2800, 150, 250, -700, 11, -2),
    ("DMO202", "B738", "N-DM202", 5200, 230, 100, 1800, -4, 2),
    ("DMO303", "A359", "N-DM303", 36000, 480, 40, 0, -9, -8),
    ("N123DM", "C172", "N123DM", 2400, 95, 330, 0, 6, 7),
    ("DMO404", "", "", 9000, None, None, -1200, -10, 5),
    ("DMO505", "E75L", "N-DM505", "ground", 0, 90, 0, -7, 3),
    ("", "B06", "N-HELI", 1500, 70, 200, 100, 3, -5),
)


def demo_aircraft(seconds):
    """DEMO_PLANES as adsb.lol style records, moved on by `seconds`."""
    seconds = seconds % 300
    records = []
    for call, kind, reg, alt, gs, trk, vr, e0, n0 in DEMO_PLANES:
        e, n = float(e0), float(n0)
        if gs and trk is not None and alt != "ground":
            e += gs / 3600.0 * math.sin(math.radians(trk)) * seconds
            n += gs / 3600.0 * math.cos(math.radians(trk)) * seconds
        rec = {
            "hex": "demo%d" % len(records),
            "alt_baro": alt,
            "lat": LAT + n / 60.0,
            "lon": LON + e / (60.0 * math.cos(math.radians(LAT))),
        }
        if call:
            rec["flight"] = call + "  "
        if kind:
            rec["t"] = kind
        if reg:
            rec["r"] = reg
        if gs is not None:
            rec["gs"] = gs
        if trk is not None:
            rec["track"] = trk
        if vr and alt != "ground":
            rec["baro_rate"] = vr
        records.append(rec)
    return records


# ---- state

planes = []                 # nearest first
selected_id = None
selected = 0
radar = False
fetched_at = None
fault = None
next_positions = 0
next_net = 0
route_pause_until = 0
route_cache = {}            # callsign -> route dict, or None for unknown
route_order = []
busy = None                 # text for the bar while a request blocks the frame
view = 0                    # index into VIEWS
started = badge.ticks


def set_planes(records):
    """Parse records, keep the nearest MAX_PLANES and sort them by distance."""
    global planes, selected, fetched_at, fault
    kept = []
    for a in records:
        p = parse_aircraft(a, LAT, LON)
        if p is not None:
            kept.append(p)
    kept.sort(key=lambda p: p["dist"])
    planes = kept[:MAX_PLANES]
    for p in planes:
        refresh_kind(p)
    selected = 0
    for i, p in enumerate(planes):
        if p["id"] == selected_id:
            selected = i
    pick(0)
    fetched_at = badge.ticks
    fault = None


def refresh_kind(p):
    p["route"] = route_cache.get(p["callsign"])
    p["kind"], p["why"] = classify(p, p["route"])


def pick(step):
    """Move the selection, keeping it in range and remembering which aircraft."""
    global selected, selected_id
    if not planes:
        selected = 0
        selected_id = None
        return
    selected = max(0, min(len(planes) - 1, selected + step))
    selected_id = planes[selected]["id"]


def remember_route(callsign, route):
    if callsign not in route_cache:
        route_order.append(callsign)
        if len(route_order) > MAX_ROUTES:
            route_cache.pop(route_order.pop(0), None)
    route_cache[callsign] = route
    for p in planes:
        if p["callsign"] == callsign:
            refresh_kind(p)


def wanted_route():
    """The callsign to look up next: the selected aircraft, then nearest first."""
    order = []
    if planes:
        order.append(planes[selected])
    order.extend(planes[:ROUTE_TOP])
    for p in order:
        if is_flight(p["callsign"]) and not p["ground"] and p["callsign"] not in route_cache:
            return p["callsign"]
    return None


def get_json(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    try:
        if response.status_code != 200:
            raise OSError("HTTP %d" % response.status_code)
        return response.json()
    finally:
        response.close()


def fetch_positions():
    global fault, next_positions
    try:
        data = get_json(POSITIONS % (LAT, LON, RADIUS))
        records = data["ac"]
        set_planes(records)
        next_positions = badge.ticks + REFRESH_MS
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
        fault = "%s %s" % (type(e).__name__, e)
        refused = "429" in str(e)
        next_positions = badge.ticks + (RATE_PAUSE_MS if refused else RETRY_MS)
    records = None
    data = None
    gc.collect()


def fetch_route(callsign):
    global route_pause_until
    response = requests.get(ROUTES + callsign, headers=HEADERS, timeout=TIMEOUT)
    try:
        if response.status_code == 404:
            remember_route(callsign, None)
            return
        if response.status_code != 200:
            route_pause_until = badge.ticks + ROUTE_PAUSE_MS
            return
        remember_route(callsign, parse_route(response.json()))
    except (OSError, ValueError, TypeError, RuntimeError):
        route_pause_until = badge.ticks + ROUTE_PAUSE_MS
    finally:
        response.close()


def go_demo(reason):
    global DEMO, demo_reason, fault, next_positions
    DEMO = True
    demo_reason = reason
    fault = None
    next_positions = 0
    route_cache.update(DEMO_ROUTES)
    route_order.extend(DEMO_ROUTES)


def blocking(text, request, *args):
    """Run a request that stalls the frame loop, showing `text` first.

    A request can take seconds and nothing is drawn or read meanwhile, so the
    frame is presented before it starts.
    """
    global busy
    busy = text
    draw_frame()
    display.update()
    try:
        request(*args)
    finally:
        busy = None


def network_step():
    """At most one blocking request per call, spaced out by the timers."""
    global next_net, fault, next_positions, wifi_wait_from
    now = badge.ticks
    if DEMO:
        if now >= next_positions:
            set_planes(demo_aircraft((now - started) // 1000))
            next_positions = now + DEMO_REFRESH_MS
        return
    if now < next_net:
        return
    if wifi is None:
        go_demo("no wifi module, demo data")
        return
    if not wifi.is_connected():
        if wifi_wait_from is None:
            wifi_wait_from = now
        if not wifi.connect():
            fault = wifi.status()[1]
        if now - wifi_wait_from > WIFI_WAIT_MS:
            go_demo("no wifi, demo data")
        return
    wifi_wait_from = None
    if now >= next_positions:
        blocking("fetching aircraft...", fetch_positions)
        next_net = badge.ticks + ROUTE_GAP_MS
    elif now >= route_pause_until:
        callsign = wanted_route()
        if callsign:
            blocking("route %s..." % callsign, fetch_route, callsign)
            next_net = badge.ticks + ROUTE_GAP_MS


if DEMO:
    go_demo(demo_reason)


# ---- drawing


def fit(text, width):
    """text cut so it fits in width pixels."""
    while text and screen.measure_text(text)[0] > width:
        text = text[:-1]
    return text


def put(text, x, y, pen):
    screen.pen = pen
    screen.text(text, x, y)


def put_right(text, right, y, pen):
    screen.pen = pen
    screen.text(text, right - screen.measure_text(text)[0], y)


def route_text(p):
    """"LHR > MIA" using ? for an end that is not known, or None with no route."""
    r = p["route"]
    if not r:
        return None
    return "%s > %s" % (r["o"][0] if r["o"] else "?", r["d"][0] if r["d"] else "?")


def ago_text(seconds):
    return "%ds" % seconds if seconds < 100 else "%dm" % (seconds // 60)


def overhead_index():
    """Index of the nearest airborne aircraft if it is within OVERHEAD_NM."""
    for i, p in enumerate(planes):
        if not p["ground"]:
            return i if p["dist"] <= OVERHEAD_NM else None
    return None


def draw_bar():
    screen.pen = BAR_BG
    screen.rectangle(0, 0, W, BAR)
    put("overhead", 4, 1, LIME)
    if busy:
        text, pen = fit(busy, 190), LIME
    elif fault:
        text, pen = fit(fault, 190), RED
    elif DEMO:
        text, pen = fit(demo_reason, 190), AMBER
    elif fetched_at is None:
        text, pen = "loading...", DIM
    else:
        ago = (badge.ticks - fetched_at) // 1000
        text, pen = "%d nm  %d ac  %s ago" % (RADIUS, len(planes), ago_text(ago)), DIM
    put_right(text, W - 4, 1, pen)


def draw_list():
    put("CALL", 4, BAR + 2, DIM)
    put("TYPE", 70, BAR + 2, DIM)
    put_right("ALT", 134, BAR + 2, DIM)
    put_right("KT", LIST_W - 4, BAR + 2, DIM)
    top = BAR + 18
    rows = (H - top) // ROW_H
    first = max(0, min(selected - rows // 2, len(planes) - rows))
    over = overhead_index()
    for i in range(first, min(len(planes), first + rows)):
        p = planes[i]
        y = top + (i - first) * ROW_H
        if i == over:
            screen.pen = AMBER
            screen.rectangle(0, y - 1, 2, ROW_H)
        if i == selected:
            screen.pen = GRID
            screen.rectangle(0, y - 1, LIST_W, ROW_H)
        pen = KIND_PEN.get(p["kind"], WHITE)
        put(fit(p["call"], 62), 4, y, pen if i != selected else LIME)
        put(p["type"] or "?", 70, y, WHITE)
        put_right(alt_text(p["alt"], p["ground"]), 134, y, WHITE)
        gs = "%d" % round(p["gs"]) if p["gs"] is not None else "--"
        put_right(gs, LIST_W - 4, y, DIM)
    if not planes:
        put("no aircraft in range", 4, top, DIM)


def draw_row(label, value, y, pen=WHITE):
    put(label, LIST_W + 6, y, DIM)
    put_right(fit(value, W - LIST_W - 50), W - 4, y, pen)


def draw_detail():
    screen.pen = GRID
    screen.rectangle(LIST_W, BAR, 1, H - BAR)
    if not planes:
        return
    p = planes[selected]
    x = LIST_W + 6
    put(fit(p["call"], 80), x, BAR + 4, LIME)
    put_right(p["kind"], W - 4, BAR + 4, KIND_PEN.get(p["kind"], WHITE))

    y = BAR + 26
    text = route_text(p)
    if text:
        put(text, x, y, WHITE)
        r = p["route"]
        for end in (r["o"], r["d"]):
            y += 14
            put(fit(end[3] or end[0] if end else "unknown", W - x - 4), x, y, DIM)
        y += 8
    else:
        known = p["callsign"] in route_cache or p["ground"] or not is_flight(p["callsign"])
        put("no route" if known else "route...", x, y, DIM)
        y += 8
    y += 14

    draw_row("type", type_name(p["type"]), y)
    y += 14
    draw_row("alt", alt_text(p["alt"], p["ground"]) + (" ft" if p["alt"] is not None and p["alt"] < 18000 else ""), y)
    y += 14
    draw_row("speed", "%d kt" % round(p["gs"]) if p["gs"] is not None else "--", y)
    y += 14
    draw_row("heading", "%03d %s" % (round(p["trk"]) % 360, compass(p["trk"])) if p["trk"] is not None else "--", y)
    y += 14
    draw_row("v rate", "%+d fpm" % p["vr"] if p["vr"] is not None else "--", y)
    y += 14
    draw_row("range", "%.1f nm %s" % (p["dist"], compass(p["brg"])), y)
    y += 14
    closing = closing_kt(p)
    if closing is not None:
        draw_row("closing" if closing >= 0 else "opening", "%d kt" % abs(round(closing)), y)
        y += 14
    draw_row("basis", p["why"], y, DIM)
    y += 14
    if p["reg"] or p["mil"]:
        draw_row("reg", (p["reg"] + " MIL").strip() if p["mil"] else p["reg"], y, DIM)


def arrow(x, y, trk, size, pen):
    screen.pen = pen
    if trk is None:
        screen.shape(shape.circle(x, y, size * 0.5))
        return
    t = math.radians(trk)
    tip = vec2(x + math.sin(t) * size, y - math.cos(t) * size)
    left = vec2(x + math.sin(t + 2.5) * size * 0.8, y - math.cos(t + 2.5) * size * 0.8)
    right = vec2(x + math.sin(t - 2.5) * size * 0.8, y - math.cos(t - 2.5) * size * 0.8)
    screen.shape(shape.custom([tip, left, vec2(x, y), right]))


def draw_radar():
    cx, cy = W // 2, BAR + (H - BAR) // 2
    r = (H - BAR) // 2 - 6
    view_nm = max(1, round(RADIUS * VIEWS[view]))
    scale = r / view_nm
    for k in (1, 2, 3):
        screen.pen = GRID
        screen.shape(shape.arc(cx, cy, r * k / 3 - 1, r * k / 3, 0, 360))
    screen.line(cx - r, cy, cx + r, cy)
    screen.line(cx, cy - r, cx, cy + r)
    put("N", cx + 4, cy - r - 2, DIM)
    put_right("%d nm" % view_nm, W - 4, BAR + 2, DIM)

    elapsed = 0.0
    if fetched_at is not None:
        elapsed = min(EXTRAPOLATE_S, (badge.ticks - fetched_at) / 1000.0)
    sel = None
    for i, p in enumerate(planes):
        e = p["e"] + p["ve"] * elapsed
        n = p["n"] + p["vn"] * elapsed
        x, y = cx + e * scale, cy - n * scale
        if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
            continue
        if i == selected:
            sel = (x, y)
            continue
        arrow(x, y, p["trk"], 5, KIND_PEN.get(p["kind"], WHITE))
    if sel:
        p = planes[selected]
        arrow(sel[0], sel[1], p["trk"], 8, LIME)
        screen.pen = LIME
        screen.shape(shape.arc(sel[0], sel[1], 11, 12, 0, 360))
        label = fit(p["call"], 70)
        w = screen.measure_text(label)[0]
        lx = sel[0] + 14 if sel[0] + 14 + w < W else sel[0] - 14 - w
        put(label, lx, sel[1] - 6, LIME)

    # you, in the centre
    screen.pen = WHITE
    screen.shape(shape.circle(cx, cy, 3))

    if planes:
        p = planes[selected]
        bits = [p["call"], p["type"], alt_text(p["alt"], p["ground"]), p["kind"], route_text(p)]
        line = fit("  ".join(b for b in bits if b), W - 8)
        screen.pen = BAR_BG
        screen.rectangle(0, H - BAR, W, BAR)
        put(line, 4, H - BAR + 1, WHITE)


def draw_frame():
    screen.pen = BG
    screen.clear()
    if radar:
        draw_radar()
    else:
        draw_list()
        draw_detail()
    draw_bar()


def update():
    global radar, view
    if badge.pressed(BUTTON_UP):
        pick(-1)
    if badge.pressed(BUTTON_DOWN):
        pick(1)
    if badge.pressed(BUTTON_SELECT):
        radar = not radar
    if radar and badge.pressed(BUTTON_RIGHT):
        view = min(len(VIEWS) - 1, view + 1)
    if radar and badge.pressed(BUTTON_LEFT):
        view = max(0, view - 1)
    network_step()
    draw_frame()

    if badge.pressed(BUTTON_BACK):
        reset()


run(update)
