"""Host-side tests for the aprs_map packet parser and trail logic.

Run: python3 editor/tests/test_aprs_map.py
Execs only the block between the "pure logic" markers of the app, so no badge globals are needed.
"""
import errno
import math
import os
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "aprs_map.py")
text = open(SRC).read()
block = text.split("# ---- pure logic start")[1].split("# ---- pure logic end")[0]
block = block.split("\n", 1)[1]
ns = {"math": math}
exec(block, ns)
parse_packet = ns["parse_packet"]
Store, Station, TRAIL_N = ns["Store"], ns["Station"], ns["TRAIL_N"]


# LiveFeed needs the app's socket and select modules, so it gets fakes
live_src = text.split("class LiveFeed:")[1].split("DEMO_CARS = (")[0]


class FakeSocket:
    def __init__(self, script):
        self.script = list(script)     # bytes to return, or an exception to raise, one per recv
        self.sent = b""
        self.closed = False
        self.connect_error = None

    def setblocking(self, flag):
        pass

    def connect(self, addr):
        raise OSError(self.connect_error or errno.EINPROGRESS)

    def send(self, data):
        self.sent += data
        return len(data)

    def recv(self, n):
        item = self.script.pop(0) if self.script else OSError(errno.EAGAIN)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class FakePoller:
    events = 4      # POLLOUT

    def register(self, sock, flags):
        pass

    def unregister(self, sock):
        pass

    def poll(self, timeout):
        return [(None, FakePoller.events)] if FakePoller.events else []


def make_feed(sock, resolve=None):
    fake_socket = types.SimpleNamespace(
        getaddrinfo=resolve or (lambda host, port: [(0, 0, 0, "", ("1.2.3.4", port))]),
        socket=lambda: sock)
    fake_select = types.SimpleNamespace(poll=FakePoller, POLLOUT=4, POLLERR=8, POLLHUP=16)
    env = {"errno": errno, "socket": fake_socket, "select": fake_select, "HOST": "h", "PORT": 1,
           "LOGIN": "user BADGE pass -1 vers x 0.1 filter r/1/2/3\r\n", "RETRY_MS": 15_000,
           "LOGIN_TIMEOUT": 8_000, "SILENT_MS": 90_000, "MAX_LINES": 8}
    FakePoller.events = 4
    exec("class LiveFeed:" + live_src, env)
    return env["LiveFeed"]()


def go_live(feed, now=0):
    feed.step(now)      # idle -> connecting
    feed.step(now)      # connecting -> login
    feed.step(now)      # login -> live
    assert feed.state == "live", feed.state


def near(a, b, tol=0.001):
    return abs(a - b) < tol


class Defaults(unittest.TestCase):
    def test_default_venue(self):
        self.assertIn("DEFAULT_LAT = 37.8068139014702", text)
        self.assertIn("DEFAULT_LON = -122.43150989195377", text)


class Parser(unittest.TestCase):
    def test_plain(self):
        p = parse_packet("TEST-1>APRS,TCPIP*:!4903.50N/07201.75W-Test")
        self.assertEqual(p["call"], "TEST-1")
        self.assertTrue(near(p["lat"], 49.058333))
        self.assertTrue(near(p["lon"], -72.029167))
        self.assertIsNone(p["course"])

    def test_plain_south_east_and_motion(self):
        p = parse_packet("A>APRS:=3351.00S/15112.50E>088/036")
        self.assertTrue(near(p["lat"], -33.85))
        self.assertTrue(near(p["lon"], 151.208333))
        self.assertEqual(p["course"], 88)
        self.assertTrue(near(p["speed"], 36 * 1.852))

    def test_timestamp_and_overlay_symbol_table(self):
        p = parse_packet("K0FJ-1>APRS:@281355z3923.37N/10102.72W_294/002g006t050")
        self.assertTrue(near(p["lat"], 39.389500))
        self.assertTrue(near(p["lon"], -101.045333))
        # real packet seen on the feed: overlay letter instead of / or \
        p = parse_packet("NQ8H-B>APCHP0,TCPIP*,qAC,X:!4036.67NW09822.68Wi RNG0001")
        self.assertTrue(near(p["lat"], 40.611167))
        self.assertTrue(near(p["lon"], -98.378))

    def test_ambiguity_spaces(self):
        p = parse_packet("A>APRS:!49  .  N/072  .  W-")
        self.assertTrue(near(p["lat"], 49.0))
        self.assertTrue(near(p["lon"], -72.0))

    def test_compressed_roundtrip(self):
        enc = ns["encode_compressed"]
        for lat, lon in ((49.5, -72.75), (-33.85, 151.2), (0.0, 0.0), (51.5, -0.12)):
            p = parse_packet(enc("X", lat, lon, 90, 20))
            self.assertTrue(near(p["lat"], lat, 0.0001), (lat, p))
            self.assertTrue(near(p["lon"], lon, 0.0001), (lon, p))
            self.assertEqual(p["course"], 88)  # 90 rounds down to a multiple of 4
            self.assertTrue(abs(p["speed"] / 1.852 - 20) < 2)

    def test_compressed_aprs101_example(self):
        # APRS101 chapter 9 sample: lat 49.5, lon -72.75, 88 deg, ~36 kn
        p = parse_packet("A>APRS:=/5L!!<*e7>7P[")
        self.assertTrue(near(p["lat"], 49.5, 0.001))
        self.assertTrue(near(p["lon"], -72.75, 0.001))
        self.assertEqual(p["course"], 88)

    def test_plain_roundtrip(self):
        enc = ns["encode_plain"]
        for lat, lon in ((12.3456, 45.6789), (-0.5, -179.9), (89.99, 0.01)):
            p = parse_packet(enc("X", lat, lon, 270, 5))
            self.assertTrue(near(p["lat"], lat, 0.0002))
            self.assertTrue(near(p["lon"], lon, 0.0002))

    def test_object(self):
        p = parse_packet("SRC>APRS:;LEADER   *092345z4903.50N/07201.75W>088/036")
        self.assertEqual(p["call"], "LEADER")
        self.assertTrue(near(p["lat"], 49.058333))
        self.assertIsNone(parse_packet("SRC>APRS:;LEADER   _092345z4903.50N/07201.75W>"))

    def test_mice(self):
        # built from the Mic-E rules: dest chars 4..6 carry N/S, +100 lon offset, W/E flags
        # lat 33 25.64 N, lon 112 07.74 W, 20 kn, 251 deg
        p = parse_packet("T>S32UVT,WIDE1-1:`(_fn\"Oj/>")
        self.assertTrue(near(p["lat"], 33 + 25.64 / 60, 0.0001), p)
        self.assertTrue(near(p["lon"], -(112 + 7.74 / 60), 0.0001), p)
        self.assertEqual(p["course"], 251)
        self.assertTrue(near(p["speed"], 20 * 1.852))
        # south and east: lat flag clear (0-9), east flag clear, no offset, lon 12 07.74 E
        p = parse_packet("T>S32U60,x:'(_fn\"Oj/>".replace("U", "5"))
        self.assertTrue(p["lat"] < 0 and p["lon"] > 0, p)

    def test_mice_live_packet(self):
        # captured from APRS-IS, decoded by hand from the Mic-E rules: dest 3X0PRU is lat 38 00.25 N,
        # offset +100 and west, info gives lon 122 06.33 W, speed 0, course 135
        p = parse_packet("KS4FUN-12>3X0PRU,W6CX-3*,WIDE2-1,qAR,K6MRP-10:`2^=l!?c/'\"4:}CERT ICP|+,%s'X|!wtO!|3")
        self.assertTrue(near(p["lat"], 38 + 0.25 / 60, 0.0001), p)
        self.assertTrue(near(p["lon"], -(122 + 6.33 / 60), 0.0001), p)
        self.assertEqual(p["course"], 135)
        self.assertEqual(p["speed"], 0)

    def test_third_party(self):
        p = parse_packet("GATE>APRS,TCPIP*:}W1AW-9>APRS,TCPIP,GATE*:!4903.50N/07201.75W-")
        self.assertEqual(p["call"], "W1AW-9")
        self.assertTrue(near(p["lat"], 49.058333))
        # nested third-party is not followed
        self.assertIsNone(parse_packet("A>B:}C>D:}E>F:!4903.50N/07201.75W-"))

    def test_item(self):
        p = parse_packet("SRC>APRS:)AID #2!4903.50N/07201.75WA")
        self.assertEqual(p["call"], "AID #2")
        self.assertTrue(near(p["lon"], -72.029167))
        self.assertIsNone(parse_packet("SRC>APRS:)AID #2_4903.50N/07201.75WA"))

    def test_weather_symbol_has_no_course(self):
        # the 090/005 is wind direction and speed, not motion
        p = parse_packet("WX>APRS:=4903.50N/07201.75W_090/005g010t068")
        self.assertTrue(near(p["lat"], 49.058333))
        self.assertIsNone(p["course"])
        self.assertIsNone(p["speed"])

    def test_compressed_altitude_is_not_speed(self):
        # type byte '2' (0x32-33=0x1d, bits 3-4 = 10) means c/s hold altitude
        p = parse_packet("A>APRS:=/5L!!<*e7>S!2")
        self.assertTrue(near(p["lat"], 49.5, 0.001))
        self.assertIsNone(p["course"])

    def test_captured_feed(self):
        # Optional: a local capture of ~100 lines from APRS-IS (r/37.8068/-122.4315/50) saved as
        # data/aprs_is_capture.txt. Not committed, it holds other people's callsigns and positions.
        # Two lines have no position (a status and a plain message), every other line must parse.
        path = os.path.join(HERE, "data", "aprs_is_capture.txt")
        if not os.path.exists(path):
            self.skipTest("no local capture at data/aprs_is_capture.txt")
        with open(path) as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
        self.assertGreater(len(lines), 90)
        rejected = [l for l in lines if parse_packet(l) is None]
        self.assertEqual(len(rejected), 2, rejected)
        for l in lines:
            p = parse_packet(l)
            if p:
                dx, dy = ns["km_from"](p["lat"], p["lon"], 37.8068, -122.4315, math.cos(math.radians(37.8068)))
                self.assertLess(math.hypot(dx, dy), 55, l)      # inside the 50 km filter

    def test_garbage(self):
        for line in ("", "# aprsc 2.1", "no colon", "A>B:", "A>APRS:>status text",
                     "A>APRS:!49xx.50N/07201.75W-", "A>APRS:!", "A>APRS:=/short",
                     "A>APRS:!9903.50N/07201.75W-", "A>APRS:`short"):
            self.assertIsNone(parse_packet(line), line)


class Trails(unittest.TestCase):
    def pkt(self, call, lat, lon):
        return {"call": call, "lat": lat, "lon": lon, "course": None, "speed": None}

    def test_ring_wraps_and_orders(self):
        s = Station("X")
        for i in range(TRAIL_N + 5):
            s.add(10 + i * 0.01, 20.0, None, None, i)
        self.assertEqual(s.n, TRAIL_N)
        lats = [s.point(i)[0] for i in range(s.n)]
        self.assertEqual(lats, sorted(lats))
        self.assertTrue(near(lats[-1], 10 + (TRAIL_N + 4) * 0.01))
        self.assertTrue(near(lats[0], 10 + 5 * 0.01))

    def test_jitter_adds_no_point_but_updates_seen(self):
        s = Station("X")
        s.add(10.0, 20.0, None, None, 1)
        s.add(10.00001, 20.0, None, None, 99)
        self.assertEqual(s.n, 1)
        self.assertEqual(s.seen, 99)

    def test_store_cap_expire_rank(self):
        st = Store()
        for i in range(ns["MAX_STATIONS"] + 10):
            st.add(self.pkt("S%d" % i, 10 + i * 0.001, 20.0), i)
        self.assertEqual(len(st.stations), ns["MAX_STATIONS"])
        self.assertNotIn("S0", st.stations)           # oldest evicted
        self.assertIn("S%d" % (ns["MAX_STATIONS"] + 9), st.stations)
        r = st.ranked(10.0, 20.0)
        self.assertEqual([k for k, _ in r], sorted(k for k, _ in r))
        st.expire(10_000, 100)
        self.assertEqual(len(st.stations), 0)


class Live(unittest.TestCase):
    def test_login_goes_out_after_connect_and_partial_lines_join(self):
        sock = FakeSocket([b"# aprsc 2.1\r\nA>APRS:!4903.50N/072", b"01.75W-x\r\nB>AP", b"RS:>hi\r\n"])
        feed = make_feed(sock)
        go_live(feed)
        self.assertTrue(sock.sent.startswith(b"user BADGE pass -1"))
        lines = feed.step(10) + feed.step(20) + feed.step(30)
        self.assertEqual(lines, ["# aprsc 2.1", "A>APRS:!4903.50N/07201.75W-x", "B>APRS:>hi"])

    def test_waits_for_connect(self):
        sock = FakeSocket([])
        feed = make_feed(sock)
        feed.step(0)
        feed.step(0)
        FakePoller.events = 0
        feed.step(100)
        self.assertEqual(feed.state, "login")
        self.assertEqual(sock.sent, b"")
        feed.step(8_001)
        self.assertEqual((feed.state, feed.error), ("idle", "connect timeout"))
        self.assertTrue(sock.closed)

    def test_connect_error_flag(self):
        sock = FakeSocket([])
        feed = make_feed(sock)
        feed.step(0)
        feed.step(0)
        FakePoller.events = 8
        feed.step(100)
        self.assertEqual(feed.error, "connect refused")

    def test_dns_failure_backs_off(self):
        def boom(host, port):
            raise OSError(-2)
        feed = make_feed(FakeSocket([]), boom)
        feed.step(0)
        feed.step(0)
        self.assertEqual(feed.state, "idle")
        self.assertEqual(feed.next_try, 15_000)
        feed.step(15_000)
        feed.step(15_000)
        self.assertEqual(feed.next_try, 15_000 + 30_000)
        feed.step(100_000)      # next_try passed, state moves on, no network used yet
        self.assertEqual(feed.state, "connecting")

    def test_login_refused_is_a_failure(self):
        # aprsc stays connected after this line, so waiting for a close would never end
        sock = FakeSocket([b"# aprsc 2.1\r\n# Login by user not allowed\r\n"])
        feed = make_feed(sock)
        go_live(feed)
        self.assertEqual(feed.step(10), [])
        self.assertEqual((feed.state, feed.error), ("idle", "login refused by server"))

    def test_server_closed_and_silence(self):
        feed = make_feed(FakeSocket([b""]))
        go_live(feed)
        feed.step(5)
        self.assertEqual(feed.error, "server closed")
        feed = make_feed(FakeSocket([]))
        go_live(feed)
        feed.step(89_000)
        self.assertEqual(feed.state, "live")
        feed.step(90_001)
        self.assertEqual(feed.error, "no data")

    def test_burst_is_limited_per_step_and_nothing_is_lost(self):
        burst = b"".join(b"A>APRS:>%d\r\n" % i for i in range(20))
        feed = make_feed(FakeSocket([burst]))
        go_live(feed)
        got = feed.step(1)
        self.assertEqual(len(got), 8)
        got += feed.step(2) + feed.step(3)
        self.assertEqual(len(got), 20)

    def test_non_utf8_comment_keeps_the_line(self):
        feed = make_feed(FakeSocket([b"A>APRS:!4903.50N/07201.75W-caf\xe9\r\n"]))
        go_live(feed)
        lines = feed.step(1)
        self.assertEqual(len(lines), 1)
        self.assertTrue(near(parse_packet(lines[0])["lat"], 49.058333))


if __name__ == "__main__":
    unittest.main()
