import errno
import math

try:
    import secrets
except ImportError:
    secrets = None

try:
    import wifi
except ImportError:
    wifi = None

try:
    import socket
except ImportError:
    socket = None

# ---- pure logic start (no badge globals below this line until "pure logic end") ----

HOST = "rotate.aprs2.net"
PORT = 14580
TRAIL_N = 10            # points kept per station
MAX_STATIONS = 40
MIN_MOVE_KM = 0.02      # smaller moves are GPS jitter, no new trail point
KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON = 111.32
KN_TO_KMH = 1.852


def base91(text):
    value = 0
    for ch in text:
        value = value * 91 + (ord(ch) - 33)
    return value


def course_speed(text):
    """'088/036' (course/knots) from the start of a comment, or (None, None)."""
    if len(text) >= 7 and text[3] == "/" and text[:3].isdigit() and text[4:7].isdigit():
        return int(text[:3]), int(text[4:7]) * KN_TO_KMH
    return None, None


def parse_plain(body):
    """Uncompressed position: DDMM.mmN/DDDMM.mmW then symbol and comment."""
    if len(body) < 19 or body[4] != "." or body[7] not in "NS":
        return None
    if body[14] != "." or body[17] not in "EW":
        return None
    lat = int(body[0:2].replace(" ", "0")) + float(body[2:7].replace(" ", "0")) / 60
    lon = int(body[9:12].replace(" ", "0")) + float(body[12:17].replace(" ", "0")) / 60
    if body[7] == "S":
        lat = -lat
    if body[17] == "W":
        lon = -lon
    course, speed = course_speed(body[19:])
    return lat, lon, course, speed


def parse_compressed(body):
    """Compressed position: table, 4 chars lat, 4 chars lon, symbol, course/speed, type."""
    if len(body) < 13:
        return None
    for ch in body[1:13]:
        if not 33 <= ord(ch) <= 124 and ch != " ":
            return None
    lat = 90 - base91(body[1:5]) / 380926
    lon = -180 + base91(body[5:9]) / 190463
    course = speed = None
    if body[10] != " ":
        c = ord(body[10]) - 33
        if 0 <= c <= 89:
            course = c * 4
            speed = (1.08 ** (ord(body[11]) - 33) - 1) * KN_TO_KMH
    return lat, lon, course, speed


def parse_body(body):
    if not body:
        return None
    if body[0] in "0123456789 ":
        return parse_plain(body)
    if body[0] in "/\\ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij":
        return parse_compressed(body)
    return None


def parse_mice(dest, info):
    """Mic-E: latitude is in the destination field, longitude and motion in the info."""
    if len(dest) < 6 or len(info) < 9:
        return None
    digits = []
    for ch in dest[:6]:
        if "0" <= ch <= "9":
            digits.append(ord(ch) - 48)
        elif "A" <= ch <= "J":
            digits.append(ord(ch) - 65)
        elif "P" <= ch <= "Y":
            digits.append(ord(ch) - 80)
        elif ch in "KLZ":
            digits.append(0)
        else:
            return None
    north = "P" <= dest[3] <= "Z"
    offset = "P" <= dest[4] <= "Z"
    west = "P" <= dest[5] <= "Z"
    lat = digits[0] * 10 + digits[1] + (digits[2] * 10 + digits[3] + (digits[4] * 10 + digits[5]) / 100) / 60
    deg = ord(info[1]) - 28
    if offset:
        deg += 100
    if deg >= 190:
        deg -= 190
    elif deg >= 180:
        deg -= 80
    minutes = ord(info[2]) - 28
    if minutes >= 60:
        minutes -= 60
    hundredths = ord(info[3]) - 28
    if not (0 <= deg <= 180 and 0 <= minutes < 60 and 0 <= hundredths < 100 and lat <= 90):
        return None
    lon = deg + (minutes + hundredths / 100) / 60
    speed = (ord(info[4]) - 28) * 10 + (ord(info[5]) - 28) // 10
    course = ((ord(info[5]) - 28) % 10) * 100 + (ord(info[6]) - 28)
    if speed >= 800:
        speed -= 800
    if course >= 400:
        course -= 400
    if not north:
        lat = -lat
    if west:
        lon = -lon
    return lat, lon, course, speed * KN_TO_KMH


def parse_packet(line):
    """TNC2 line -> dict(call, lat, lon, course, speed in km/h) or None."""
    if not line or line[0] == "#":
        return None
    head, sep, info = line.partition(":")
    if not sep or not info or ">" not in head:
        return None
    call, _, route = head.partition(">")
    dest = route.split(",")[0]
    kind = info[0]
    try:
        if kind in "!=":
            fix = parse_body(info[1:])
        elif kind in "/@":
            fix = parse_body(info[8:])
        elif kind == ";":
            # object: ;NAME_____*DDHHMMzPOSITION, '_' means the object was killed
            if len(info) < 19 or info[10] != "*":
                return None
            call = info[1:10].strip()
            fix = parse_body(info[18:])
        elif kind in "`'":
            fix = parse_mice(dest, info)
        else:
            return None
    except (ValueError, IndexError):
        return None
    if fix is None or not (-90 <= fix[0] <= 90 and -180 <= fix[1] <= 180):
        return None
    return {"call": call, "lat": fix[0], "lon": fix[1], "course": fix[2], "speed": fix[3]}


def km_from(lat, lon, lat0, lon0, cos0):
    """East and north offset in km from the centre, flat-earth, fine for a few hundred km."""
    return (lon - lon0) * cos0 * KM_PER_DEG_LON, (lat - lat0) * KM_PER_DEG_LAT


class Station:
    def __init__(self, call):
        self.call = call
        self.lat = self.lon = 0.0
        self.speed = self.course = None
        self.seen = 0
        self.lats = [0.0] * TRAIL_N
        self.lons = [0.0] * TRAIL_N
        self.head = 0
        self.n = 0

    def add(self, lat, lon, course, speed, now):
        self.seen = now
        self.speed = speed
        self.course = course
        if self.n:
            dx, dy = km_from(lat, lon, self.lat, self.lon, math.cos(math.radians(lat)))
            if dx * dx + dy * dy < MIN_MOVE_KM * MIN_MOVE_KM:
                return
        self.lat = lat
        self.lon = lon
        self.lats[self.head] = lat
        self.lons[self.head] = lon
        self.head = (self.head + 1) % TRAIL_N
        if self.n < TRAIL_N:
            self.n += 1

    def point(self, i):
        """The i-th trail point, 0 is the oldest."""
        j = (self.head - self.n + i) % TRAIL_N
        return self.lats[j], self.lons[j]


class Store:
    def __init__(self):
        self.stations = {}

    def add(self, packet, now):
        call = packet["call"]
        station = self.stations.get(call)
        if station is None:
            if len(self.stations) >= MAX_STATIONS:
                oldest = min(self.stations.values(), key=lambda s: s.seen)
                del self.stations[oldest.call]
            station = self.stations[call] = Station(call)
        station.add(packet["lat"], packet["lon"], packet["course"], packet["speed"], now)

    def expire(self, now, max_age):
        for call in [c for c, s in self.stations.items() if now - s.seen > max_age]:
            del self.stations[call]

    def ranked(self, lat0, lon0):
        """Stations nearest the centre first, as (km, station)."""
        cos0 = math.cos(math.radians(lat0))
        out = []
        for s in self.stations.values():
            dx, dy = km_from(s.lat, s.lon, lat0, lon0, cos0)
            out.append((math.sqrt(dx * dx + dy * dy), s))
        out.sort(key=lambda item: item[0])
        return out


def encode_plain(call, lat, lon, course, speed_kn):
    def part(value, pos, neg, width):
        hundredths = int(round(abs(value) * 6000))
        deg, rest = divmod(hundredths, 6000)
        return "%0*d%02d.%02d%s" % (width, deg, rest // 100, rest % 100, pos if value >= 0 else neg)
    return "%s>APRS,TCPIP*:!%s/%s>%03d/%03d" % (
        call, part(lat, "N", "S", 2), part(lon, "E", "W", 3), course % 360, speed_kn)


def encode_compressed(call, lat, lon, course, speed_kn):
    def b91(value, size):
        out = ""
        for _ in range(size):
            value, digit = divmod(value, 91)
            out = chr(33 + digit) + out
        return out
    y = int(round((90 - lat) * 380926))
    x = int(round((180 + lon) * 190463))
    s = int(round(math.log(speed_kn + 1) / math.log(1.08)))
    return "%s>APRS,TCPIP*:!/%s%s>%s%s[" % (
        call, b91(y, 4), b91(x, 4), chr(33 + (course % 360) // 4), chr(33 + s))

# ---- pure logic end ----

badge.mode(HIRES | VSYNC)
screen.font = font.sins
screen.antialias = image.X2

W, H = screen.width, screen.height
BAR = 16
MAP_TOP, MAP_BOT = BAR, H - BAR
MAP_CX, MAP_CY = W // 2, (MAP_TOP + MAP_BOT) // 2

LOGIN_TIMEOUT = 4           # seconds, the connect and banner read block one frame
RETRY_MS = 15_000
SILENT_MS = 90_000          # servers send a keepalive about every 20 s
EXPIRE_MS = 30 * 60_000
WIFI_WAIT_MS = 10_000       # no wifi after this long: fall back to demo
MAX_LINES = 8               # packets parsed per frame
LABELS = 4
ZOOMS = (1.0, 0.5, 0.25, 0.1)   # fraction of the radius shown from centre to the map edge
NICE_KM = (0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500)

BG = color.rgb(10, 14, 12)
BAR_BG = color.rgb(22, 27, 34)
WHITE = color.rgb(235, 245, 255)
DIM = color.rgb(139, 148, 158)
GREEN = color.rgb(46, 200, 87)
LIME = color.rgb(211, 250, 55)
AMBER = color.rgb(240, 180, 40)
RED = color.rgb(248, 81, 73)
GRID = color.rgb(30, 40, 36)


def setting(key):
    return getattr(secrets, key, "") if secrets else ""


def number(key):
    try:
        return float(str(setting(key)).strip())
    except ValueError:
        return None


# GitHub Universe 2026 venue, used unless MAP_LAT and MAP_LON are both set
DEFAULT_LAT = 37.8068139014702
DEFAULT_LON = -122.43150989195377

LAT = number("MAP_LAT")
LON = number("MAP_LON")
RADIUS = number("MAP_RADIUS_KM") or 25.0
RADIUS = max(1.0, min(RADIUS, 500.0))
VALID = LAT is not None and LON is not None and -90 <= LAT <= 90 and -180 <= LON <= 180
if not VALID:
    LAT, LON = DEFAULT_LAT, DEFAULT_LON
COS0 = math.cos(math.radians(LAT))

CALL = str(setting("APRS_CALLSIGN") or "").strip().upper() or "N0CALL"
PASSCODE = str(setting("APRS_PASSCODE") or "").strip() or "-1"
LOGIN = "user %s pass %s vers badge-aprs-map 0.1 filter r/%.4f/%.4f/%d\r\n" % (
    CALL, PASSCODE, LAT, LON, int(RADIUS))


class LiveFeed:
    """APRS-IS over a TCP socket. Non-blocking reads, one short blocking connect."""

    def __init__(self):
        self.sock = None
        self.buf = b""
        self.state = "idle"
        self.next_try = 0
        self.last_rx = 0
        self.error = None

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.buf = b""

    def fail(self, now, reason):
        self.close()
        self.state = "idle"
        self.error = reason
        self.next_try = now + RETRY_MS

    def step(self, now):
        """Returns up to MAX_LINES decoded lines."""
        if self.state == "idle":
            if now >= self.next_try:
                self.state = "connecting"   # draw one frame saying so, then block
            return []
        if self.state == "connecting":
            try:
                addr = socket.getaddrinfo(HOST, PORT)[0][-1]
                s = socket.socket()
                self.sock = s
                s.settimeout(LOGIN_TIMEOUT)
                s.connect(addr)
                s.recv(256)                 # server banner
                s.send(LOGIN.encode())
                s.setblocking(False)
            except (OSError, IndexError) as e:
                self.fail(now, "connect: %s" % e)
                return []
            self.state = "live"
            self.error = None
            self.last_rx = now
            return []
        try:
            data = self.sock.recv(1024)
        except OSError as e:
            if e.args and e.args[0] == errno.EAGAIN:
                data = None
            else:
                self.fail(now, "recv: %s" % e)
                return []
        if data is not None:
            if not data:
                self.fail(now, "server closed")
                return []
            self.last_rx = now
            self.buf = (self.buf + data)[-3000:]
        elif now - self.last_rx > SILENT_MS:
            self.fail(now, "no data")
            return []
        lines = []
        while len(lines) < MAX_LINES:
            i = self.buf.find(b"\n")
            if i < 0:
                break
            raw, self.buf = self.buf[:i], self.buf[i + 1:]
            try:
                lines.append(raw.decode().strip())
            except (ValueError, UnicodeError):
                pass
        return lines


DEMO_CARS = (
    # call, east km, north km, km/h, heading deg
    ("DEMO1", 0.25, 0.20, 50, 80),
    ("DEMO2", -0.40, 0.10, 35, 200),
    ("DEMO3", 0.10, -0.45, 70, 310),
    ("DEMO4", -0.15, -0.20, 20, 45),
    ("DEMO5", 0.50, -0.10, 90, 170),
    ("DEMO6", -0.55, 0.45, 40, 260),
    ("DEMO7", 0.05, 0.60, 60, 130),
)
DEMO_EVERY_MS = 1500
DEMO_SIM_HOURS = 45 / 3600  # each tick moves a car as if 45 s passed


class DemoFeed:
    """Canned stations driving around the centre, fed through the same parser."""

    def __init__(self):
        self.state = "demo"
        self.error = None
        self.next_at = 0
        self.cars = []
        for call, e, n, kmh, heading in DEMO_CARS:
            self.cars.append([call, e * RADIUS, n * RADIUS, kmh, heading])

    def close(self):
        pass

    def step(self, now):
        if now < self.next_at:
            return []
        self.next_at = now + DEMO_EVERY_MS
        lines = []
        for i, car in enumerate(self.cars):
            call, e, n, kmh, heading = car
            step = kmh * DEMO_SIM_HOURS
            e += step * math.sin(math.radians(heading))
            n += step * math.cos(math.radians(heading))
            if e * e + n * n > (RADIUS * 0.9) ** 2:
                heading = (heading + 180) % 360
            car[1], car[2], car[4] = e, n, heading
            lat = LAT + n / KM_PER_DEG_LAT
            lon = LON + e / (KM_PER_DEG_LON * COS0)
            knots = kmh / KN_TO_KMH
            make = encode_plain if i % 2 == 0 else encode_compressed
            lines.append(make(call, lat, lon, heading, knots))
        return lines


store = Store()
feed = None
demo_reason = None          # why demo is on, shown in the footer
wifi_wait_from = None
zoom = 0
selected = None             # callsign
ranked = []
ranked_at = 0


def start_feed(demo, reason=None):
    global feed, demo_reason, selected
    if feed:
        feed.close()
    store.stations.clear()
    selected = None
    demo_reason = reason
    feed = DemoFeed() if demo else LiveFeed()


if setting("APRS_DEMO"):
    start_feed(True, "demo (APRS_DEMO set)")
elif socket is None or wifi is None:
    start_feed(True, "no sockets here, demo")
else:
    start_feed(False)


def wifi_ready(now):
    """True when live mode may use the network. Drives wifi.connect() a step per frame."""
    global wifi_wait_from
    if wifi.is_connected():
        wifi_wait_from = None
        return True
    if wifi_wait_from is None:
        wifi_wait_from = now
    if not wifi.connect():
        feed.error = wifi.status()[1]
    if now - wifi_wait_from > WIFI_WAIT_MS:
        start_feed(True, "no wifi, demo")
    return False


def nice_scale(px_per_km):
    best = NICE_KM[0]
    for km in NICE_KM:
        if km * px_per_km <= 90:
            best = km
    return best


def view_centre():
    if zoom and selected:
        s = store.stations.get(selected)
        if s:
            return s.lat, s.lon
    return LAT, LON


def draw_map(now):
    view_km = RADIUS * ZOOMS[zoom]
    px_km = (MAP_BOT - MAP_TOP) / 2 / view_km
    clat, clon = view_centre()
    cos_c = math.cos(math.radians(clat))

    def xy(lat, lon):
        e, n = km_from(lat, lon, clat, clon, cos_c)
        return MAP_CX + e * px_km, MAP_CY - n * px_km

    screen.pen = BG
    screen.rectangle(0, MAP_TOP, W, MAP_BOT - MAP_TOP)

    # range ring at the filter radius and the centre mark
    vx, vy = xy(LAT, LON)
    screen.pen = GRID
    screen.shape(shape.arc(vx, vy, RADIUS * px_km - 1, RADIUS * px_km, 0, 360))
    screen.line(vx - 4, vy, vx + 4, vy)
    screen.line(vx, vy - 4, vx, vy + 4)

    ordered = [s for _, s in ranked]
    label_calls = [s.call for s in ordered[:LABELS]]
    if selected:
        label_calls.append(selected)

    for s in store.stations.values():
        is_sel = s.call == selected
        n = s.n
        for i in range(1, n):
            x0, y0 = xy(*s.point(i - 1))
            x1, y1 = xy(*s.point(i))
            fade = 40 + int(200 * i / n)
            screen.pen = color.rgb(211, 250, 55, fade) if is_sel else color.rgb(46, 200, 87, fade)
            screen.shape(shape.line(vec2(x0, y0), vec2(x1, y1), 1.6))

    for s in store.stations.values():
        x, y = xy(s.lat, s.lon)
        if not (0 <= x < W and MAP_TOP <= y < MAP_BOT):
            continue
        is_sel = s.call == selected
        old = now - s.seen > 600_000
        screen.pen = LIME if is_sel else (DIM if old else GREEN)
        screen.shape(shape.circle(x, y, 4 if is_sel else 3))
        if s.call in label_calls:
            screen.pen = WHITE if is_sel else DIM
            screen.text(s.call, x + 6, y - 6)
    return px_km


def draw_bars(px_km, now):
    screen.pen = BAR_BG
    screen.rectangle(0, 0, W, BAR)
    screen.rectangle(0, H - BAR, W, BAR)

    screen.pen = WHITE
    screen.text("APRS %d stn" % len(store.stations), 4, 1)
    if feed.state == "live":
        screen.pen = GREEN
        status = "live"
    elif feed.state == "demo":
        screen.pen = AMBER
        status = "DEMO"
    elif feed.state == "connecting":
        screen.pen = AMBER
        status = "connecting..."
    else:
        screen.pen = RED
        status = (feed.error or "waiting")[:28]
    width, _ = screen.measure_text(status)
    screen.text(status, W - width - 4, 1)

    y = H - BAR + 1
    km = nice_scale(px_km)
    length = km * px_km
    screen.pen = WHITE
    screen.line(4, H - 5, 4 + length, H - 5)
    screen.line(4, H - 8, 4, H - 3)
    screen.line(4 + length, H - 8, 4 + length, H - 3)
    label = ("%d m" % int(km * 1000)) if km < 1 else ("%g km" % km)
    screen.pen = DIM
    screen.text(label, 10 + length, y - 1)

    s = store.stations.get(selected) if selected else None
    if s:
        text = s.call
        if s.speed is not None:
            text += " %d km/h" % s.speed
        if s.course is not None:
            text += " %d" % s.course
        screen.pen = LIME
    elif demo_reason:
        text = demo_reason
        screen.pen = AMBER
    else:
        text = "Select zoom, Up/Down pick"
        screen.pen = DIM
    width, _ = screen.measure_text(text)
    screen.text(text, W - width - 4, y)


def handle_input():
    global zoom, selected
    if badge.pressed(BUTTON_SELECT):
        zoom = (zoom + 1) % len(ZOOMS)
    if badge.pressed(BUTTON_LEFT):
        selected = None
    if badge.pressed(BUTTON_RIGHT) and socket and wifi:
        # toggle between live and demo
        start_feed(not isinstance(feed, DemoFeed), "demo (Right to go live)")
    step = (badge.pressed(BUTTON_DOWN) and 1) or (badge.pressed(BUTTON_UP) and -1) or 0
    if step and ranked:
        calls = [s.call for _, s in ranked]
        if selected in calls:
            selected = calls[(calls.index(selected) + step) % len(calls)]
        else:
            selected = calls[0] if step > 0 else calls[-1]


def update():
    global ranked, ranked_at
    now = badge.ticks
    handle_input()

    if badge.pressed(BUTTON_BACK):
        feed.close()
        reset()

    if isinstance(feed, LiveFeed) and not wifi_ready(now):
        pass
    else:
        for line in feed.step(now):
            packet = parse_packet(line)
            if packet:
                store.add(packet, now)

    if now - ranked_at > 500:
        store.expire(now, EXPIRE_MS)
        ranked = store.ranked(LAT, LON)
        ranked_at = now

    px_km = draw_map(now)
    draw_bars(px_km, now)


run(update)
