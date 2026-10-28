"""Host-side tests for the aprs_beacon packet builder and Sender state machine.

Run: python3 editor/tests/test_aprs_beacon.py
Execs only the block between the "pure logic" markers of the app, and drives Sender with a fake
socket, so nothing here touches the network and no real callsign or passcode is used.
"""
import errno
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "aprs_beacon.py")
text = open(SRC).read()
block = text.split("# ---- pure logic start")[1].split("# ---- pure logic end")[0]
block = block.split("\n", 1)[1]
ns = {"errno": errno}
exec(block, ns)

valid_callsign = ns["valid_callsign"]
valid_passcode = ns["valid_passcode"]
clean_comment = ns["clean_comment"]
encode_lat, encode_lon = ns["encode_lat"], ns["encode_lon"]
build_packet = ns["build_packet"]
login_line = ns["login_line"]
parse_logresp = ns["parse_logresp"]
wait_ms = ns["wait_ms"]
Sender = ns["Sender"]

CALL = "T3ST-7"     # made up for the tests
PASS = "12345"


class Callsigns(unittest.TestCase):
    def test_accepts(self):
        for c in ("K1ABC", "W1AW", "N0XYZ-7", "VK2ABC", "G4ABC-15", "2E0ABC", "KD9XYZ-0"):
            self.assertTrue(valid_callsign(c), c)

    def test_refuses_placeholders_and_junk(self):
        for c in ("", "N0CALL", "NOCALL", "BADGE", "N0CALL-9", "BADGE-1", "MYCALL", "YOURCALL", "k1abc",
                  "AB", "ABCDEFG1", "ABCDEF", "123456", "K1ABC-16", "K1ABC-", "K1ABC-X", "K1 ABC",
                  "K1ABC-123", "K1ABC>X", "K1ABC,X"):
            self.assertFalse(valid_callsign(c), c)

    def test_passcode(self):
        for p in ("0", "12345", "32767", " 4242 "):
            self.assertTrue(valid_passcode(p), p)
        for p in ("", "-1", "32768", "123456", "abc", "12 3", "1.5", None):
            self.assertFalse(valid_passcode(p), p)


class Encoding(unittest.TestCase):
    def test_lat_lon(self):
        self.assertEqual(encode_lat(37.8068139), "3748.41N")
        self.assertEqual(encode_lon(-122.4315099), "12225.89W")
        self.assertEqual(encode_lat(-33.8688), "3352.13S")
        self.assertEqual(encode_lon(151.2093), "15112.56E")
        self.assertEqual(encode_lat(0.0), "0000.00N")
        self.assertEqual(encode_lon(0.0), "00000.00E")

    def test_rounding_carries_into_degrees(self):
        # 49 59.999 minutes rounds up to 50 00.00, never 4960.00
        self.assertEqual(encode_lat(49 + 59.9999 / 60), "5000.00N")
        self.assertEqual(encode_lon(-(7 + 59.9999 / 60)), "00800.00W")

    def test_extremes(self):
        self.assertEqual(encode_lat(90.0), "9000.00N")
        self.assertEqual(encode_lat(-90.0), "9000.00S")
        self.assertEqual(encode_lon(180.0), "18000.00E")
        self.assertEqual(encode_lon(-180.0), "18000.00W")

    def test_every_field_has_fixed_width(self):
        import random
        random.seed(1)
        for _ in range(500):
            lat, lon = random.uniform(-90, 90), random.uniform(-180, 180)
            self.assertEqual(len(encode_lat(lat)), 8, lat)
            self.assertEqual(len(encode_lon(lon)), 9, lon)

    def test_packet(self):
        p = build_packet(CALL, 37.8068139, -122.4315099, "GitHub Universe 2026 badge")
        self.assertEqual(p, "T3ST-7>APZUNV,TCPIP*:!3748.41N/12225.89W[GitHub Universe 2026 badge")

    def test_comment_is_cleaned(self):
        self.assertEqual(clean_comment("a|b~c{d"), "abcd")
        self.assertEqual(clean_comment("café ☃ ok\r\n"), "caf  ok")
        self.assertEqual(len(clean_comment("x" * 100)), 43)
        self.assertEqual(clean_comment(""), "")

    def test_packet_has_no_control_characters(self):
        p = build_packet(CALL, 1.0, 2.0, "line\r\nbreak\x00")
        self.assertTrue(all(" " <= c <= "~" for c in p), repr(p))


class Login(unittest.TestCase):
    def test_login_line(self):
        self.assertEqual(login_line(CALL, PASS), "user T3ST-7 pass 12345 vers badge-aprs-beacon 0.1\r\n")

    def test_logresp(self):
        self.assertEqual(parse_logresp("# logresp T3ST-7 verified, server T2TEST", CALL), "verified")
        self.assertEqual(parse_logresp("# logresp T3ST-7 unverified, server T2TEST", CALL), "unverified")
        self.assertEqual(parse_logresp("# logresp t3st-7 verified, server X", CALL), "verified")
        self.assertIsNone(parse_logresp("# logresp OTHER verified, server X", CALL))
        self.assertIsNone(parse_logresp("# aprsc 2.1.19", CALL))
        self.assertIsNone(parse_logresp("", CALL))

    def test_wait_ms(self):
        self.assertEqual(wait_ms(1000, None, 10), 0)
        self.assertEqual(wait_ms(60_000, 0, 10), 540_000)
        self.assertEqual(wait_ms(700_000, 0, 10), 0)


class FakeSock:
    def __init__(self, script, send_limit=None):
        self.script = list(script)      # chunks, or OSError instances, returned by recv
        self.sent = b""
        self.closed = False
        self.send_limit = send_limit
        self.connect_error = None

    def setblocking(self, flag):
        pass

    def connect(self, addr):
        if self.connect_error:
            raise self.connect_error
        raise OSError(errno.EINPROGRESS)

    def send(self, data):
        if self.send_limit:
            data = data[:self.send_limit]
        self.sent += data
        return len(data)

    def recv(self, n):
        if not self.script:
            raise OSError(errno.EAGAIN)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class FakePoller:
    def __init__(self, events):
        self.events = events

    def register(self, sock, mask):
        pass

    def unregister(self, sock):
        pass

    def poll(self, timeout):
        return self.events


class FakeSelect:
    POLLOUT, POLLERR, POLLHUP = 4, 8, 16

    def __init__(self, events=None):
        self.events = [(None, 4)] if events is None else events

    def poll(self):
        return FakePoller(self.events)


class FakeSocketModule:
    def __init__(self, sock, dns_error=None):
        self.sock = sock
        self.dns_error = dns_error

    def getaddrinfo(self, host, port):
        if self.dns_error:
            raise self.dns_error
        return [(0, 0, 0, "", ("127.0.0.1", port))]

    def socket(self):
        return self.sock


def run_sender(script, select_events=None, send_limit=None, ticks=60, dns_error=None, connect_error=None, step_ms=100):
    sock = FakeSock(script, send_limit)
    sock.connect_error = connect_error
    packet = build_packet(CALL, 37.8068139, -122.4315099, "test")
    s = Sender(CALL, PASS, packet, FakeSocketModule(sock, dns_error), FakeSelect(select_events))
    s.start(0)
    now = 0
    for _ in range(ticks):
        now += step_ms
        s.step(now)
        if s.state in ("done", "failed"):
            break
    return s, sock, packet


class SenderFlow(unittest.TestCase):
    def test_verified_login_sends_the_packet_once_and_closes(self):
        s, sock, packet = run_sender([b"# aprsc 2.1.19\r\n", b"# logresp T3ST-7 verified, server T2X\r\n"])
        self.assertEqual(s.state, "done", s.error)
        lines = sock.sent.decode().split("\r\n")
        self.assertEqual(lines[0], "user T3ST-7 pass 12345 vers badge-aprs-beacon 0.1")
        self.assertEqual(lines[1], packet)
        self.assertEqual(lines[2], "")
        self.assertEqual(sock.sent.count(b"APZUNV"), 1)
        self.assertTrue(sock.closed)

    def test_unverified_login_sends_no_packet(self):
        s, sock, _ = run_sender([b"# logresp T3ST-7 unverified, server T2X\r\n"])
        self.assertEqual(s.state, "failed")
        self.assertIn("not verified", s.error)
        self.assertNotIn(b"APZUNV", sock.sent)
        self.assertTrue(sock.closed)

    def test_refused_login_sends_no_packet(self):
        s, sock, _ = run_sender([b"# Login by user not allowed\r\n"])
        self.assertEqual(s.state, "failed")
        self.assertNotIn(b"APZUNV", sock.sent)

    def test_login_answer_split_across_reads(self):
        s, sock, packet = run_sender([b"# logresp T3ST", b"-7 verified, ser", b"ver X\r\n"])
        self.assertEqual(s.state, "done", s.error)
        self.assertIn(packet.encode(), sock.sent)

    def test_partial_sends_are_finished(self):
        s, sock, packet = run_sender([b"# logresp T3ST-7 verified, server X\r\n"], send_limit=7, ticks=400)
        self.assertEqual(s.state, "done", s.error)
        self.assertTrue(sock.sent.endswith(packet.encode() + b"\r\n"))

    def test_server_closes_early(self):
        s, sock, _ = run_sender([b""])
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.error, "server closed")

    def test_no_answer_times_out_without_sending(self):
        s, sock, _ = run_sender([], ticks=400, step_ms=100)
        self.assertEqual(s.state, "failed")
        self.assertIn("timeout", s.error)
        self.assertNotIn(b"APZUNV", sock.sent)
        self.assertTrue(sock.closed)

    def test_dns_failure(self):
        s, sock, _ = run_sender([], dns_error=OSError(-2, "gai"))
        self.assertEqual(s.state, "failed")
        self.assertTrue(s.error.startswith("connect:"))

    def test_connect_refused_event(self):
        s, sock, _ = run_sender([], select_events=[(None, 8)])
        self.assertEqual(s.state, "failed")
        self.assertEqual(s.error, "connect refused")

    def test_connect_error_other_than_in_progress(self):
        s, sock, _ = run_sender([], connect_error=OSError(errno.ECONNREFUSED))
        self.assertEqual(s.state, "failed")

    def test_recv_error(self):
        s, sock, _ = run_sender([OSError(errno.ECONNRESET)])
        self.assertEqual(s.state, "failed")
        self.assertTrue(s.error.startswith("recv:"))

    def test_nothing_happens_before_start_or_after_failure(self):
        sock = FakeSock([])
        s = Sender(CALL, PASS, "P", FakeSocketModule(sock), FakeSelect())
        s.step(1000)
        self.assertEqual(s.state, "idle")
        self.assertEqual(sock.sent, b"")

    def test_passcode_never_appears_in_the_packet_or_errors(self):
        for script in ([b"# logresp T3ST-7 unverified, server X\r\n"], [b""], []):
            s, sock, packet = run_sender(script, ticks=300)
            self.assertNotIn(PASS, packet)
            self.assertNotIn(PASS, s.error or "")
            # the passcode is only ever in the first (login) line
            self.assertNotIn(PASS.encode(), b"\r\n".join(sock.sent.split(b"\r\n")[1:]))


class AppSource(unittest.TestCase):
    """Checks on the app text, since the app part needs badge globals to run."""

    def test_default_venue(self):
        self.assertIn("DEFAULT_LAT = 37.8068139014702", text)
        self.assertIn("DEFAULT_LON = -122.43150989195377", text)

    def test_never_draws_the_passcode_or_login(self):
        app = text.split("# ---- pure logic end ----")[1]
        for line in app.splitlines():
            if "put(" in line or "row(" in line or "screen.text" in line:
                self.assertNotIn("PASSCODE", line, line)
                self.assertNotIn("login_line", line, line)

    def test_interval_floor_is_enforced(self):
        self.assertIn("INTERVAL_MIN = max(MIN_INTERVAL_MIN,", text)

    def test_auto_is_off_at_start_and_needs_confirm(self):
        self.assertIn("auto_on = False", text)
        self.assertIn('confirm = "auto"', text)

    def test_files_in_sync(self):
        app = os.path.join(HERE, "..", "..", "badge", "apps", "aprs_beacon", "__init__.py")
        with open(app) as f:
            self.assertEqual(f.read(), text)


if __name__ == "__main__":
    unittest.main()
