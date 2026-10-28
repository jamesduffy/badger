"""Host tests for the overhead app. Run: python3 -I editor/tests/test_overhead.py

Uses real adsb.lol and adsbdb responses saved under tests/data (taken
2026-10-28, test point 51.47, -0.45, 25 nm). The app is exec'd with stand-in
badge globals, so nothing here proves anything about the hardware.
"""
import json
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
APP = os.path.join(HERE, "..", "overhead.py")
TEST_LAT, TEST_LON = 51.47, -0.45


def load(path=APP, secrets_values=None):
    sys.modules["requests"] = types.ModuleType("requests")
    sec = types.ModuleType("secrets")
    for k, v in (secrets_values or {}).items():
        setattr(sec, k, v)
    sys.modules["secrets"] = sec
    sys.modules.pop("wifi", None)
    ticks = {"t": 0}
    badge = mock.MagicMock()
    badge.ticks = 0
    badge.pressed.return_value = False
    screen = mock.MagicMock()
    screen.measure_text.side_effect = lambda s: (len(s) * 6, 8)
    ns = {
        "badge": badge, "screen": screen, "font": mock.MagicMock(),
        "image": mock.MagicMock(), "color": mock.MagicMock(),
        "shape": mock.MagicMock(), "vec2": lambda x, y: (x, y),
        "run": lambda f: None, "reset": mock.MagicMock(),
        "HIRES": 1, "VSYNC": 2, "BUTTON_UP": "up", "BUTTON_DOWN": "down",
        "BUTTON_SELECT": "sel", "BUTTON_BACK": "back", "BUTTON_LEFT": "left",
        "BUTTON_RIGHT": "right", "display": mock.MagicMock(), "__name__": "overhead_app",
    }
    with open(path) as f:
        exec(compile(f.read(), path, "exec"), ns)
    return ns


def j(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


class Maths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load()

    def test_known_points(self):
        a = self.app
        # one degree of latitude is 60 nm
        self.assertAlmostEqual(a["distance_nm"](0, 0, 1, 0), 60.04, delta=0.1)
        self.assertAlmostEqual(a["bearing_deg"](0, 0, 1, 0), 0, delta=0.01)
        self.assertAlmostEqual(a["bearing_deg"](0, 0, 0, 1), 90, delta=0.01)
        self.assertAlmostEqual(a["bearing_deg"](0, 0, -1, 0), 180, delta=0.01)
        self.assertAlmostEqual(a["bearing_deg"](0, 0, 0, -1), 270, delta=0.01)

    def test_matches_adsblol_dst_and_dir(self):
        """adsb.lol returns dst (nm) and dir (deg) from the query point."""
        a = self.app
        checked = 0
        for rec in j("overhead_adsblol.json")["ac"]:
            if "lat" not in rec or "dst" not in rec:
                continue
            d = a["distance_nm"](TEST_LAT, TEST_LON, rec["lat"], rec["lon"])
            b = a["bearing_deg"](TEST_LAT, TEST_LON, rec["lat"], rec["lon"])
            self.assertAlmostEqual(d, rec["dst"], delta=0.3, msg=rec["hex"])
            diff = abs((b - rec["dir"] + 180) % 360 - 180)
            self.assertLess(diff, 1.0, rec["hex"])
            checked += 1
        self.assertGreater(checked, 50)

    def test_compass_and_alt(self):
        a = self.app
        self.assertEqual(a["compass"](0), "N")
        self.assertEqual(a["compass"](359), "N")
        self.assertEqual(a["compass"](45), "NE")
        self.assertEqual(a["compass"](225), "SW")
        self.assertEqual(a["alt_text"](None, True), "GND")
        self.assertEqual(a["alt_text"](None, False), "--")
        self.assertEqual(a["alt_text"](3200, False), "3200")
        self.assertEqual(a["alt_text"](24450, False), "FL245")


class Parsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load()
        cls.raw = j("overhead_adsblol.json")["ac"]

    def test_all_real_records_parse(self):
        parsed = [self.app["parse_aircraft"](r, TEST_LAT, TEST_LON) for r in self.raw]
        self.assertEqual(len([p for p in parsed if p]), len(self.raw))
        for p in parsed:
            self.assertTrue(p["call"])
            self.assertTrue(0 <= p["brg"] < 360)

    def test_ground_and_missing_fields(self):
        parse = self.app["parse_aircraft"]
        ground = [r for r in self.raw if r.get("alt_baro") == "ground"]
        self.assertTrue(ground)
        p = parse(ground[0], TEST_LAT, TEST_LON)
        self.assertTrue(p["ground"])
        self.assertIsNone(p["alt"])
        no_gs = [r for r in self.raw if "gs" not in r]
        self.assertTrue(no_gs)  # real record without ground speed
        p = parse(no_gs[0], TEST_LAT, TEST_LON)
        self.assertIsNone(p["gs"])
        self.assertEqual(p["ve"], 0.0)
        no_flight = [r for r in self.raw if "flight" not in r]
        self.assertTrue(no_flight)
        p = parse(no_flight[0], TEST_LAT, TEST_LON)
        self.assertEqual(p["callsign"], "")
        self.assertEqual(p["call"], no_flight[0].get("r") or no_flight[0]["hex"])
        self.assertIsNone(parse({"hex": "abc"}, TEST_LAT, TEST_LON))

    def test_callsign_stripped_and_heading_fallback(self):
        parse = self.app["parse_aircraft"]
        rec = next(r for r in self.raw if r.get("flight", "").startswith("GEMLG"))
        p = parse(rec, TEST_LAT, TEST_LON)
        self.assertEqual(p["callsign"], "GEMLG")
        self.assertEqual(p["trk"], rec["true_heading"])  # no `track` on the ground
        rec = next(r for r in self.raw if "track" in r and "baro_rate" not in r and "geom_rate" in r) \
            if any("track" in r and "baro_rate" not in r and "geom_rate" in r for r in self.raw) else None
        if rec:
            self.assertEqual(parse(rec, TEST_LAT, TEST_LON)["vr"], rec["geom_rate"])

    def test_velocity_components(self):
        p = self.app["parse_aircraft"](
            {"hex": "x", "lat": 0.1, "lon": 0.0, "gs": 360, "track": 90, "alt_baro": 1000}, 0.0, 0.0)
        self.assertAlmostEqual(p["ve"], 0.1, places=6)  # 360 kt is 0.1 nm/s
        self.assertAlmostEqual(p["vn"], 0.0, places=6)
        self.assertAlmostEqual(p["n"], 6.0, delta=0.05)

    def test_routes(self):
        pr = self.app["parse_route"]
        r = pr(j("overhead_adsbdb_TAP1359.json"))
        self.assertEqual(r["o"][0], "LHR")
        self.assertEqual(r["d"][0], "LIS")
        self.assertEqual(r["d"][3], "Lisbon")
        r = pr(j("overhead_adsbdb_GBRDM.json"))
        self.assertEqual(r["o"][0], "GBA")   # no iata_code, icao used? iata is GBA
        self.assertEqual(r["d"][0], "EGLM")  # iata_code null, falls back to icao
        self.assertIsNone(pr(j("overhead_adsbdb_unknown.json")))
        self.assertIsNone(pr({}))
        self.assertIsNone(pr(None))


class Classify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load()
        cls.raw = {r["flight"].strip(): r for r in j("overhead_adsblol.json")["ac"] if r.get("flight")}

    def kind(self, call, route_file=None):
        p = self.app["parse_aircraft"](self.raw[call], TEST_LAT, TEST_LON)
        route = self.app["parse_route"](j(route_file)) if route_file else None
        return self.app["classify"](p, route)

    def test_real_arrival_by_route(self):
        # BAW8 Tokyo to LHR, level at 8000 ft 12 nm out (holding or sequencing)
        self.assertEqual(self.kind("BAW8", "overhead_adsbdb_BAW8.json"), ("ARR", "route"))

    def test_real_stale_route_overridden_by_vertical_rate(self):
        # adsbdb has BAW211 as Miami to LHR, but the aircraft is at 775 ft
        # climbing 2560 fpm 2 nm from LHR, so it is a departure
        self.assertEqual(self.kind("BAW211", "overhead_adsbdb_BAW211.json"), ("DEP", "vertical rate"))

    def test_real_departure_by_route(self):
        # TAP1359 LHR to Lisbon
        self.assertEqual(self.kind("TAP1359", "overhead_adsbdb_TAP1359.json")[0], "DEP")

    def test_route_wins_over_weak_vertical_rate(self):
        k, why = self.kind("TAP1359", "overhead_adsbdb_TAP1359.json")
        self.assertEqual(why, "route")

    def test_real_climbing_no_route_is_not_departure_at_altitude(self):
        k, why = self.kind("RYR88AC")  # 24450 ft, +1344 fpm
        self.assertEqual((k, why), ("CLB", "vertical rate"))

    def test_ground(self):
        self.assertEqual(self.kind("GEMLG")[0], "GND")

    def test_circuit_same_field_uses_vertical_rate(self):
        k, why = self.kind("GOWAR", "overhead_adsbdb_GOWAR.json")
        self.assertEqual(why, "vertical rate")
        self.assertEqual(k, "LVL")  # baro_rate 0

    def synthetic(self, vr, alt, route=None, lat=51.47, lon=-0.45):
        p = {"ground": False, "vr": vr, "alt": alt, "lat": lat, "lon": lon}
        return self.app["classify"](p, route)

    def test_synthetic_rules(self):
        # no route
        self.assertEqual(self.synthetic(-700, 3000)[0], "ARR")
        self.assertEqual(self.synthetic(900, 3000)[0], "DEP")
        self.assertEqual(self.synthetic(-700, 30000)[0], "DSC")
        self.assertEqual(self.synthetic(900, 30000)[0], "CLB")
        self.assertEqual(self.synthetic(100, 3000)[0], "LVL")
        self.assertEqual(self.synthetic(None, 3000)[0], "LVL")
        # route says arriving at the nearby airport, but climbing hard: ignore route
        arr = {"o": ("FAR", 40.0, 10.0, "x"), "d": ("NEAR", 51.5, -0.4, "y")}
        self.assertEqual(self.synthetic(-500, 2000, arr)[0], "ARR")
        self.assertEqual(self.synthetic(0, 2000, arr)[0], "ARR")
        self.assertEqual(self.synthetic(1500, 2000, arr), ("DEP", "vertical rate"))
        dep = {"o": ("NEAR", 51.5, -0.4, "y"), "d": ("FAR", 40.0, 10.0, "x")}
        self.assertEqual(self.synthetic(1500, 2000, dep)[0], "DEP")
        self.assertEqual(self.synthetic(-1500, 2000, dep), ("ARR", "vertical rate"))
        # overflight: both ends far away, level -> LVL
        far = {"o": ("A", 40.0, 10.0, ""), "d": ("B", 60.0, -20.0, "")}
        self.assertEqual(self.synthetic(0, 36000, far)[0], "LVL")
        # one end unknown
        half = {"o": None, "d": ("NEAR", 51.5, -0.4, "y")}
        self.assertEqual(self.synthetic(0, 3000, half)[0], "ARR")
        # airport without coordinates
        nocoord = {"o": ("X", None, None, None), "d": None}
        self.assertEqual(self.synthetic(0, 3000, nocoord)[0], "LVL")


class Behaviour(unittest.TestCase):
    def test_radius_and_demo(self):
        a = load()
        self.assertFalse(a["DEMO"])
        self.assertEqual((a["LAT"], a["LON"]), (37.8068139014702, -122.43150989195377))
        self.assertEqual(a["RADIUS"], 15)
        a = load(secrets_values={"PLANES_DEMO": "1"})
        self.assertTrue(a["DEMO"])
        a = load(secrets_values={"MAP_LAT": "10.5", "MAP_LON": "-20.25", "PLANES_RADIUS_NM": 500})
        self.assertFalse(a["DEMO"])
        self.assertEqual((a["LAT"], a["LON"]), (10.5, -20.25))
        self.assertEqual(a["RADIUS"], 40)
        a = load(secrets_values={"MAP_LAT": 10, "MAP_LON": 20, "PLANES_RADIUS_NM": 1})
        self.assertEqual(a["RADIUS"], 3)
        # bad or missing values fall back to the venue, not to demo
        for values in ({"MAP_LAT": 100, "MAP_LON": 20}, {"MAP_LAT": "abc", "MAP_LON": 20},
                       {"MAP_LAT": "", "MAP_LON": ""}, {"MAP_LAT": 10}):
            a = load(secrets_values=values)
            self.assertFalse(a["DEMO"])
            self.assertEqual((a["LAT"], a["LON"]), (37.8068139014702, -122.43150989195377))

    def test_no_wifi_module_falls_back_to_demo(self):
        a = load()
        a["badge"].ticks = 1000
        a["update"]()
        self.assertTrue(a["DEMO"])
        self.assertIn("no wifi", a["demo_reason"])
        a["badge"].ticks = 2000
        a["update"]()
        self.assertEqual(len(a["planes"]), 7)

    def test_demo_planes_sit_around_the_centre(self):
        a = load(secrets_values={"PLANES_DEMO": "1"})
        for rec in a["demo_aircraft"](0):
            self.assertLess(abs(rec["lat"] - a["LAT"]), 3)
            self.assertLess(abs(rec["lon"] - a["LON"]), 3)

    def test_set_planes_from_real_response_and_cache(self):
        a = load(secrets_values={"MAP_LAT": TEST_LAT, "MAP_LON": TEST_LON, "PLANES_RADIUS_NM": 25})
        a["set_planes"](j("overhead_adsblol.json")["ac"])
        planes = a["planes"] if "planes" in a else None
        planes = a["planes"]
        self.assertEqual(len(planes), a["MAX_PLANES"])
        dists = [p["dist"] for p in planes]
        self.assertEqual(dists, sorted(dists))
        # a cached route is applied and survives a new fetch
        call = next(p["callsign"] for p in planes if p["callsign"] == "TAP1359" or p["callsign"])
        a["remember_route"](call, {"o": ("A", 0.0, 0.0, "a"), "d": None})
        self.assertIn(call, a["route_cache"])
        self.assertTrue(any(p["route"] for p in a["planes"] if p["callsign"] == call))
        # no repeat lookups: wanted_route never returns a cached callsign or a ground aircraft
        for _ in range(100):
            w = a["wanted_route"]()
            if w is None:
                break
            self.assertNotIn(w, a["route_cache"])
            a["remember_route"](w, None)
        self.assertIsNone(a["wanted_route"]())

    def test_cache_bounded(self):
        a = load()
        for i in range(a["MAX_ROUTES"] + 25):
            a["remember_route"]("C%d" % i, None)
        self.assertLessEqual(len(a["route_cache"]), a["MAX_ROUTES"])
        self.assertNotIn("C0", a["route_cache"])
        self.assertEqual(len(a["route_cache"]), len(a["route_order"]))

    def test_selection_follows_aircraft(self):
        a = load(secrets_values={"MAP_LAT": TEST_LAT, "MAP_LON": TEST_LON})
        recs = j("overhead_adsblol.json")["ac"]
        a["set_planes"](recs)
        a["pick"](3)
        chosen = a["selected_id"]
        a["set_planes"](list(reversed(recs)))
        self.assertEqual(a["planes"][a["selected"]]["id"], chosen)
        a["pick"](-100)
        self.assertEqual(a["selected"], 0)
        a["pick"](10000)
        self.assertEqual(a["selected"], len(a["planes"]) - 1)

    def test_empty_list_is_safe(self):
        a = load(secrets_values={"MAP_LAT": 1, "MAP_LON": 1})
        a["set_planes"]([])
        a["pick"](1)
        a["update"]()
        a["radar"] = True
        a["update"]()

    def test_demo_draws_both_views(self):
        a = load(secrets_values={"PLANES_DEMO": "1"})
        a["badge"].ticks = 1000
        a["update"]()
        self.assertEqual(len(a["planes"]), 7)
        kinds = {p["callsign"] or p["call"]: p["kind"] for p in a["planes"]}
        self.assertEqual(kinds["DMO101"], "ARR")
        self.assertEqual(kinds["DMO202"], "DEP")
        self.assertEqual(kinds["DMO505"], "GND")
        a["radar"] = True
        for i in range(8):
            a["badge"].pressed.side_effect = lambda b, i=i: b == "down"
            a["update"]()
        a["badge"].ticks = 60000
        a["update"]()

    def test_select_and_back(self):
        a = load()
        a["badge"].pressed.side_effect = lambda b: b == "sel"
        a["update"]()
        self.assertTrue(a["radar"])
        a["badge"].pressed.side_effect = lambda b: b == "back"
        a["update"]()
        a["reset"].assert_called()

    def test_fetch_positions_with_stub_requests(self):
        a = load(secrets_values={"MAP_LAT": TEST_LAT, "MAP_LON": TEST_LON, "PLANES_RADIUS_NM": 25})
        body = j("overhead_adsblol.json")
        seen = {}

        class R:
            status_code = 200
            def json(self): return body
            def close(self): pass

        def get(url, headers=None, timeout=None):
            seen["url"] = url
            return R()
        a["requests"].get = get
        a["fetch_positions"]()
        self.assertEqual(seen["url"], "https://api.adsb.lol/v2/point/51.4700/-0.4500/25")
        self.assertIsNone(a["fault"])
        self.assertTrue(a["planes"])

        # failure keeps old data and sets a fault
        def bad(url, headers=None, timeout=None):
            raise OSError("boom")
        a["requests"].get = bad
        n = len(a["planes"])
        a["fetch_positions"]()
        self.assertTrue(a["fault"])
        self.assertEqual(len(a["planes"]), n)

    def test_fetch_route_statuses(self):
        a = load(secrets_values={"MAP_LAT": 1, "MAP_LON": 1})
        codes = {"A": (200, j("overhead_adsbdb_TAP1359.json")), "B": (404, {"response": "unknown callsign"}),
                 "C": (429, {})}

        class R:
            def __init__(s, c, b): s.status_code, s.b = c, b
            def json(s): return s.b
            def close(s): pass
        a["requests"].get = lambda url, headers=None, timeout=None: R(*codes[url[-1]])
        a["fetch_route"]("A")
        a["fetch_route"]("B")
        self.assertEqual(a["route_cache"]["A"]["o"][0], "LHR")
        self.assertIsNone(a["route_cache"]["B"])
        self.assertIn("B", a["route_cache"])
        a["badge"].ticks = 5
        a["fetch_route"]("C")
        self.assertNotIn("C", a["route_cache"])
        self.assertGreater(a["route_pause_until"], 5)


class Blocking(unittest.TestCase):
    """A request stalls the frame loop, so a frame must be presented first."""

    def wifi_app(self):
        a = load()
        w = types.ModuleType("wifi")
        w.is_connected = lambda: True
        w.connect = lambda: True
        a["wifi"] = w
        return a

    def test_first_frame_is_shown_before_the_first_request(self):
        a = self.wifi_app()
        events = []
        a["display"].update.side_effect = lambda: events.append(("present", a["busy"]))

        def get(url, headers=None, timeout=None):
            events.append(("request", a["busy"]))
            raise OSError("stalled")
        a["requests"].get = get
        a["badge"].ticks = 1000
        a["update"]()
        self.assertEqual(events[0], ("present", "fetching aircraft..."))
        self.assertEqual(events[1], ("request", "fetching aircraft..."))
        self.assertIsNone(a["busy"])
        self.assertIn("stalled", a["fault"])
        # the screen was cleared and drawn before that first present
        self.assertTrue(a["screen"].clear.called)

    def test_route_lookup_is_announced_too(self):
        a = self.wifi_app()
        a["set_planes"](j("overhead_adsblol.json")["ac"])
        seen = []

        class R:
            status_code = 404
            def json(self): return {}
            def close(self): pass
        a["requests"].get = lambda url, headers=None, timeout=None: (seen.append(a["busy"]), R())[1]
        a["next_positions"] = 10 ** 9
        a["badge"].ticks = 5000
        a["update"]()
        self.assertTrue(seen and seen[0].startswith("route "))

    def test_429_backs_off_for_minutes(self):
        a = self.wifi_app()
        a["badge"].ticks = 1000

        class R:
            status_code = 429
            def json(self): return {}
            def close(self): pass
        a["requests"].get = lambda url, headers=None, timeout=None: R()
        a["fetch_positions"]()
        self.assertEqual(a["fault"], "OSError HTTP 429")
        self.assertEqual(a["next_positions"], 1000 + a["RATE_PAUSE_MS"])

    def test_refresh_fits_500_a_day_over_8_hours(self):
        a = load()
        self.assertLessEqual(8 * 3600 * 1000 / a["REFRESH_MS"], 300)


class SanFrancisco(unittest.TestCase):
    """Synthetic records shaped like real adsb.lol ones, around the venue."""

    @classmethod
    def setUpClass(cls):
        cls.app = load()

    def plane(self, **kw):
        rec = {"hex": "a1b2c3", "lat": 37.78, "lon": -122.40, "alt_baro": 2500,
               "gs": 160, "track": 290, "baro_rate": -700, "flight": "UAL928  ", "t": "B738"}
        rec.update(kw)
        rec = {k: v for k, v in rec.items() if v is not None}
        return self.app["parse_aircraft"](rec, 37.8068, -122.4315)

    def test_sfo_arrival_and_departure_without_route(self):
        a = self.app
        self.assertEqual(a["classify"](self.plane(), None)[0], "ARR")
        self.assertEqual(a["classify"](self.plane(baro_rate=1800), None)[0], "DEP")

    def test_sfo_route_with_iata_codes(self):
        a = self.app
        route = a["parse_route"]({"response": {"flightroute": {
            "origin": {"iata_code": "LAX", "icao_code": "KLAX", "latitude": 33.94, "longitude": -118.41, "municipality": "Los Angeles"},
            "destination": {"iata_code": "SFO", "icao_code": "KSFO", "latitude": 37.62, "longitude": -122.38, "municipality": "San Francisco"}}}})
        self.assertEqual(a["route_text"]({"route": route}), "LAX > SFO")
        self.assertEqual(a["classify"](self.plane(baro_rate=None), route), ("ARR", "route"))

    def test_helicopter_no_callsign_no_rate(self):
        p = self.plane(flight=None, t="EC35", r="N911SF", baro_rate=None, alt_baro=800, gs=70)
        self.assertEqual(p["call"], "N911SF")
        self.assertEqual(self.app["classify"](p, None), ("LVL", "no vertical rate"))
        self.assertFalse(self.app["is_flight"](p["callsign"]))

    def test_ground_string_and_missing_altitude(self):
        p = self.plane(alt_baro="ground", baro_rate=None)
        self.assertTrue(p["ground"])
        self.assertEqual(self.app["classify"](p, None)[0], "GND")
        p = self.plane(alt_baro=None)
        self.assertIsNone(p["alt"])
        self.assertEqual(self.app["alt_text"](p["alt"], p["ground"]), "--")

    def test_military_flag(self):
        self.assertTrue(self.plane(dbFlags=1)["mil"])
        self.assertFalse(self.plane(dbFlags=2)["mil"])
        self.assertFalse(self.plane()["mil"])

    def test_route_lookups_only_for_airline_callsigns(self):
        is_flight = self.app["is_flight"]
        for call in ("UAL928", "RYR88AC", "BAW8", "FDX1234"):
            self.assertTrue(is_flight(call), call)
        for call in ("", "N123AB", "GBRDM", "TWR", "ABC", "00000000"):
            self.assertFalse(is_flight(call), call)

    def test_type_names(self):
        a = self.app
        self.assertEqual(a["type_name"]("B738"), "737-800")
        self.assertEqual(a["type_name"]("E75L"), "E175")
        self.assertEqual(a["type_name"]("ZZZZ"), "ZZZZ")
        self.assertEqual(a["type_name"](""), "?")
        for name in a["TYPE_NAMES"].values():
            self.assertLessEqual(len(name), 13)

    def test_closing_speed(self):
        a = self.app
        # due east of the centre, flying west at 360 kt: closing 360
        p = a["parse_aircraft"]({"hex": "x", "lat": 0.0, "lon": 0.05, "gs": 360, "track": 270, "alt_baro": 1000}, 0.0, 0.0)
        self.assertAlmostEqual(a["closing_kt"](p), 360, delta=2)
        p["trk"] = 90
        p["ve"] = -p["ve"]
        self.assertAlmostEqual(a["closing_kt"](p), -360, delta=2)
        self.assertIsNone(a["closing_kt"](dict(p, gs=None)))

    def test_overhead_marker_picks_nearest_airborne_only(self):
        a = load()
        near_ground = {"hex": "g", "lat": a["LAT"] + 0.001, "lon": a["LON"], "alt_baro": "ground"}
        near_air = {"hex": "h", "lat": a["LAT"] + 0.01, "lon": a["LON"], "alt_baro": 1500, "gs": 60, "track": 0}
        far_air = {"hex": "i", "lat": a["LAT"] + 0.2, "lon": a["LON"], "alt_baro": 3000}
        a["set_planes"]([far_air, near_air, near_ground])
        self.assertEqual(a["planes"][a["overhead_index"]()]["id"], "h")
        a["set_planes"]([far_air])
        self.assertIsNone(a["overhead_index"]())

    def test_radar_zoom_buttons(self):
        a = load(secrets_values={"PLANES_DEMO": "1"})
        a["badge"].ticks = 1000
        a["radar"] = True
        a["badge"].pressed.side_effect = lambda b: b == "right"
        a["update"]()
        a["update"]()
        a["update"]()
        self.assertEqual(a["view"], len(a["VIEWS"]) - 1)
        a["badge"].pressed.side_effect = lambda b: b == "left"
        for _ in range(5):
            a["update"]()
        self.assertEqual(a["view"], 0)

    def test_detail_draws_for_every_demo_plane(self):
        a = load(secrets_values={"PLANES_DEMO": "1"})
        a["badge"].ticks = 1000
        for i in range(8):
            a["badge"].pressed.side_effect = lambda b: b == "down"
            a["update"]()


class FilesInSync(unittest.TestCase):
    def test_editor_copy_matches_app(self):
        app = os.path.join(HERE, "..", "..", "badge", "apps", "overhead", "__init__.py")
        with open(app) as f1, open(APP) as f2:
            self.assertEqual(f1.read(), f2.read())

    def test_no_chdir_or_syspath(self):
        with open(APP) as f:
            s = f.read()
        self.assertNotIn("chdir", s)
        self.assertNotIn("sys.path", s)


if __name__ == "__main__":
    unittest.main()
