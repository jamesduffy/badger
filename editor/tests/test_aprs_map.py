"""Host-side tests for the aprs_map packet parser and trail logic.

Run: python3 editor/tests/test_aprs_map.py
Execs only the block between the "pure logic" markers of the app, so no badge globals are needed.
"""
import math
import os
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


def near(a, b, tol=0.001):
    return abs(a - b) < tol


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


if __name__ == "__main__":
    unittest.main()
