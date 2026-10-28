import errno

try:
    import secrets
except ImportError:
    secrets = None

try:
    import wifi
except ImportError:
    wifi = None

try:
    import select
    import socket
except ImportError:
    select = socket = None

# ---- pure logic start (no badge globals below this line until "pure logic end") ----

HOST = "rotate.aprs2.net"
PORT = 14580
TOCALL = "APZUNV"           # APZ is the experimental range, this app has no registered tocall
MIN_INTERVAL_MIN = 10       # never beacon more often than this
DEFAULT_INTERVAL_MIN = 30
MAX_INTERVAL_MIN = 120
DEFAULT_COMMENT = "GitHub Universe 2026 badge"
MAX_COMMENT = 43
SYMBOL_TABLE = "/"
SYMBOL_CODE = "["           # human
# Not accepted as a callsign: the placeholders that mean "nobody".
REFUSED_CALLS = ("", "N0CALL", "NOCALL", "BADGE", "MYCALL", "YOURCALL")


def valid_callsign(call):
    """True for CALL or CALL-SSID: 3 to 6 letters or digits with at least one of each, SSID 0 to 15."""
    if not call or call != call.upper() or call in REFUSED_CALLS:
        return False
    base, _, ssid = call.partition("-")
    if base in REFUSED_CALLS or not 3 <= len(base) <= 6:
        return False
    for ch in base:
        if not ("A" <= ch <= "Z" or "0" <= ch <= "9"):
            return False
    if not any("0" <= ch <= "9" for ch in base) or not any("A" <= ch <= "Z" for ch in base):
        return False
    if "-" in call:
        if not ssid.isdigit() or len(ssid) > 2 or int(ssid) > 15:
            return False
    return True


def valid_passcode(text):
    """The APRS-IS passcode is a number from 0 to 32767. -1 means receive only, so it does not count."""
    text = str(text).strip()
    return text.isdigit() and len(text) <= 5 and int(text) <= 32767


def clean_comment(text):
    """Printable ASCII only, no APRS reserved characters, at most MAX_COMMENT long."""
    out = ""
    for ch in str(text):
        if " " <= ch <= "~" and ch not in "|~{":
            out += ch
    return out.strip()[:MAX_COMMENT]


def _dm(value, degree_digits):
    """Degrees and decimal minutes as DDMM.mm (or DDDMM.mm), rounded to 0.01 minute with carry."""
    hundredths = int(round(abs(value) * 6000))
    deg, rest = divmod(hundredths, 6000)
    minutes, frac = divmod(rest, 100)
    return "%0*d%02d.%02d" % (degree_digits, deg, minutes, frac)


def encode_lat(lat):
    return _dm(lat, 2) + ("N" if lat >= 0 else "S")


def encode_lon(lon):
    return _dm(lon, 3) + ("E" if lon >= 0 else "W")


def build_packet(call, lat, lon, comment):
    """A position report without timestamp, to be sent to APRS-IS."""
    return "%s>%s,TCPIP*:!%s%s%s%s%s" % (
        call, TOCALL, encode_lat(lat), SYMBOL_TABLE, encode_lon(lon), SYMBOL_CODE, clean_comment(comment))


def login_line(call, passcode):
    return "user %s pass %s vers badge-aprs-beacon 0.1\r\n" % (call, passcode)


def parse_logresp(line, call):
    """'verified', 'unverified' or None when the line is not a login answer for call."""
    parts = line.replace(",", " ").split()
    if len(parts) >= 4 and parts[0] == "#" and parts[1] == "logresp" and parts[2].upper() == call.upper():
        if parts[3] == "verified":
            return "verified"
        if parts[3] == "unverified":
            return "unverified"
    return None


def wait_ms(now, last_sent, interval_min):
    """Milliseconds until another beacon is allowed, 0 when it is allowed now."""
    if last_sent is None:
        return 0
    return max(0, last_sent + interval_min * 60_000 - now)


class Sender:
    """Opens APRS-IS, logs in, waits for 'verified', sends one packet, closes. Never retries on its own.

    step(now) is called once per frame. Only the DNS lookup blocks. state is one of
    idle, connecting, login, wait, send, linger, done, failed.
    """

    TIMEOUT_MS = 12_000
    LINGER_MS = 1_500

    def __init__(self, call, passcode, packet, sock_module, select_module):
        self.call = call
        self.packet = packet
        self.socket = sock_module
        self.select = select_module
        self.login = login_line(call, passcode).encode()
        self.out = b""
        self.buf = b""
        self.sock = None
        self.poller = None
        self.state = "idle"
        self.error = None
        self.started = 0

    def start(self, now):
        self.state = "connecting"
        self.error = None
        self.started = now

    def close(self):
        if self.sock:
            try:
                self.poller.unregister(self.sock)
            except (OSError, KeyError, AttributeError):
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def fail(self, reason):
        self.close()
        self.state = "failed"
        self.error = reason

    def _flush(self):
        """Send what is left in self.out. True when everything went out."""
        try:
            sent = self.sock.send(self.out)
        except OSError as e:
            if e.args and e.args[0] == errno.EAGAIN:
                return False
            self.fail("send: %s" % e)
            return False
        self.out = self.out[sent:]
        return not self.out

    def step(self, now):
        if self.state in ("idle", "done", "failed"):
            return
        if self.state != "linger" and now - self.started > self.TIMEOUT_MS:
            self.fail("timeout in %s" % self.state)
            return
        if self.state == "connecting":
            try:
                addr = self.socket.getaddrinfo(HOST, PORT)[0][-1]
                self.sock = self.socket.socket()
                self.sock.setblocking(False)
                self.poller = self.select.poll()
                self.poller.register(self.sock, self.select.POLLOUT)
                try:
                    self.sock.connect(addr)
                except OSError as e:
                    if e.args[0] not in (errno.EINPROGRESS, errno.EAGAIN):
                        raise
            except (OSError, IndexError) as e:
                self.fail("connect: %s" % e)
                return
            self.out = self.login
            self.state = "login"
            return
        if self.state == "login":
            events = self.poller.poll(0)
            if not events:
                return
            if events[0][1] & (self.select.POLLERR | self.select.POLLHUP):
                self.fail("connect refused")
                return
            if self._flush():
                self.state = "wait"
            return
        if self.state == "wait":
            try:
                data = self.sock.recv(512)
            except OSError as e:
                if e.args and e.args[0] == errno.EAGAIN:
                    return
                self.fail("recv: %s" % e)
                return
            if not data:
                self.fail("server closed")
                return
            self.buf = (self.buf + data)[-1500:]
            while True:
                i = self.buf.find(b"\n")
                if i < 0:
                    break
                raw, self.buf = self.buf[:i], self.buf[i + 1:]
                try:
                    text = raw.decode().strip()
                except (ValueError, UnicodeError):
                    continue
                answer = parse_logresp(text, self.call)
                if answer == "verified":
                    self.out = self.packet.encode() + b"\r\n"
                    self.state = "send"
                    return
                if answer == "unverified":
                    self.fail("login not verified, check the passcode")
                    return
                if text.startswith("# Login by user not allowed"):
                    self.fail("server refused this login")
                    return
            return
        if self.state == "send":
            if self._flush():
                self.state = "linger"
                self.started = now
            return
        if self.state == "linger" and now - self.started >= self.LINGER_MS:
            self.close()
            self.state = "done"


# ---- pure logic end ----

badge.mode(HIRES | VSYNC)
screen.font = font.sins
screen.antialias = image.X2

W, H = 320, 240

BG = color.rgb(13, 17, 23)
WHITE = color.rgb(235, 245, 255)
DIM = color.rgb(139, 148, 158)
GREEN = color.rgb(46, 160, 67)
RED = color.rgb(248, 81, 73)
AMBER = color.rgb(210, 153, 34)
LIME = color.rgb(211, 250, 55)

# GitHub Universe 2026 venue, used unless MAP_LAT and MAP_LON are both set
DEFAULT_LAT = 37.8068139014702
DEFAULT_LON = -122.43150989195377
WIFI_WAIT_MS = 20_000


def setting(key):
    return str(getattr(secrets, key, "") or "").strip()


def number(key):
    try:
        return float(setting(key))
    except ValueError:
        return None


CALL = setting("APRS_CALLSIGN").upper()
PASSCODE = setting("APRS_PASSCODE")
LAT = number("MAP_LAT")
LON = number("MAP_LON")
if LAT is None or LON is None or not -90 <= LAT <= 90 or not -180 <= LON <= 180:
    LAT, LON = DEFAULT_LAT, DEFAULT_LON
COMMENT = clean_comment(setting("BEACON_COMMENT") or DEFAULT_COMMENT)
try:
    INTERVAL_MIN = int(setting("BEACON_INTERVAL_MIN") or DEFAULT_INTERVAL_MIN)
except ValueError:
    INTERVAL_MIN = DEFAULT_INTERVAL_MIN
INTERVAL_MIN = max(MIN_INTERVAL_MIN, min(MAX_INTERVAL_MIN, INTERVAL_MIN))
DRY_RUN = bool(setting("APRS_DRYRUN")) or socket is None or wifi is None

PROBLEMS = []
if not valid_callsign(CALL):
    PROBLEMS.append("APRS_CALLSIGN (your own, like CALL or CALL-7)")
if not valid_passcode(PASSCODE):
    PROBLEMS.append("APRS_PASSCODE (a number, not -1)")
READY = not PROBLEMS
PACKET = build_packet(CALL, LAT, LON, COMMENT) if READY else ""

sender = None
announced = False       # the "connecting" frame has been drawn, so the blocking DNS lookup may run
confirm = None          # None, "send" or "auto" while waiting for Select
auto_on = False
next_auto = 0           # ticks when the next automatic beacon is due
last_sent = None        # ticks of the last completed beacon
last_result = ""        # shown on the home screen
wifi_wait_from = None
sent_count = 0


def fit(text, width):
    while text and screen.measure_text(text)[0] > width:
        text = text[:-1]
    return text


def put(text, x, y, pen):
    screen.pen = pen
    screen.text(text, x, y)


def put_right(text, right, y, pen):
    screen.pen = pen
    screen.text(text, right - screen.measure_text(text)[0], y)


def row(label, value, y):
    put(label, 12, y, DIM)
    put_right(fit(value, W - 110), W - 12, y, WHITE)


def start_beacon(now):
    """Begin one transmission. Dry run never opens a connection."""
    global sender, last_result, announced, next_auto
    next_auto = now + INTERVAL_MIN * 60_000
    if wait_ms(now, last_sent, MIN_INTERVAL_MIN) > 0:
        last_result = "too soon, wait %d min" % (wait_ms(now, last_sent, MIN_INTERVAL_MIN) // 60_000 + 1)
        return
    if DRY_RUN:
        finish(now, True, "")
        return
    sender = Sender(CALL, PASSCODE, PACKET, socket, select)
    announced = False
    sender.start(now)


def finish(now, ok, error):
    global sender, last_sent, last_result, sent_count
    sender = None
    if ok:
        last_sent = now
        sent_count += 1
        last_result = "dry run, nothing sent" if DRY_RUN else "sent"
    else:
        last_result = "failed: " + error


def drive_sender(now):
    global wifi_wait_from, announced
    if sender is None:
        return
    if not announced:
        announced = True    # let one frame say "connecting..." before anything blocks
        return
    if sender.state == "connecting" and wifi is not None and not wifi.is_connected():
        if wifi_wait_from is None:
            wifi_wait_from = now
        wifi.connect()
        if now - wifi_wait_from > WIFI_WAIT_MS:
            wifi_wait_from = None
            finish(now, False, "no wifi")
        return
    wifi_wait_from = None
    sender.step(now)
    if sender.state == "done":
        finish(now, True, "")
    elif sender.state == "failed":
        finish(now, False, sender.error or "unknown")


def status_text(now):
    if sender is not None:
        return {"idle": "starting", "connecting": "connecting...", "login": "logging in...",
                "wait": "waiting for login", "send": "sending...", "linger": "sending..."}.get(sender.state, "working")
    if not last_result:
        return "ready"
    if last_sent is not None and last_result in ("sent", "dry run, nothing sent"):
        return "%s %d min ago" % (last_result, (now - last_sent) // 60_000)
    return last_result


def draw_setup():
    put("APRS beacon", 12, 10, LIME)
    put("Set these in secrets.py:", 12, 44, WHITE)
    y = 70
    for item in PROBLEMS:
        put(fit(item, W - 24), 12, y, AMBER)
        y += 22
    put("Licensed operators only.", 12, y + 14, DIM)
    put("Your own callsign, never N0CALL.", 12, y + 36, DIM)


def draw_confirm():
    put("Send your position?", 12, 10, LIME)
    put("This publishes it on APRS-IS, where", 12, 44, WHITE)
    put("anyone can see it on aprs.fi.", 12, 64, WHITE)
    row("callsign", CALL, 100)
    row("position", "%.4f, %.4f" % (LAT, LON), 124)
    row("comment", COMMENT, 148)
    if confirm == "auto":
        row("repeat", "every %d min" % INTERVAL_MIN, 172)
    if DRY_RUN:
        put("Dry run: nothing is sent.", 12, 200, GREEN)
    put("SELECT confirms     BACK cancels", 12, H - 22, AMBER)


def draw_home(now):
    put("APRS beacon", 12, 10, LIME)
    put_right("dry run" if DRY_RUN else "APRS-IS", W - 12, 10, AMBER if DRY_RUN else GREEN)
    row("callsign", CALL, 44)
    row("position", "%.4f, %.4f" % (LAT, LON), 68)
    row("comment", COMMENT, 92)
    bad = last_result.startswith("failed") or last_result.startswith("too soon")
    put(fit(status_text(now), W - 24), 12, 128, RED if bad else WHITE)
    if auto_on:
        nxt = max(0, next_auto - now) // 60_000
        put("auto every %d min, next in %d" % (INTERVAL_MIN, nxt), 12, 152, GREEN)
    else:
        put("auto off", 12, 152, DIM)
    put(fit(PACKET, W - 24), 12, 184, DIM)
    put("SEL send   UP auto   BACK exit", 12, H - 22, DIM)


def update():
    global confirm, auto_on
    now = badge.ticks
    back = badge.pressed(BUTTON_BACK)
    screen.pen = BG
    screen.clear()

    if not READY:
        draw_setup()
        if back:
            reset()
        return

    busy = sender is not None
    if confirm:
        if badge.pressed(BUTTON_SELECT):
            if confirm == "auto":
                auto_on = True
            start_beacon(now)
            confirm = None
        elif back:
            confirm = None
    else:
        if badge.pressed(BUTTON_SELECT) and not busy:
            confirm = "send"
        if badge.pressed(BUTTON_UP):
            if auto_on:
                auto_on = False
            elif not busy:
                confirm = "auto"
        if auto_on and not busy and now >= next_auto:
            start_beacon(now)
        if back:
            reset()
    drive_sender(now)
    if confirm:
        draw_confirm()
    else:
        draw_home(now)


def on_exit():
    if sender is not None:
        sender.close()


run(update)
