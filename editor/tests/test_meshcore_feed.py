"""Host tests for meshcore_feed frame builders and parsers.
Run: python3 -I editor/tests/test_meshcore_feed.py

The app is exec'd with stand-in badge globals and a stub `bluetooth`. Nothing
here touches a radio or proves anything about the badge or a real node.

Where the byte vectors come from:
- LAYOUT: field offsets read from MeshCore examples/companion_radio/MyMesh.cpp
  (github.com/meshcore-dev/MeshCore, main) and cross-checked against
  meshcore_py src/meshcore/reader.py. Frames below are built by hand to those
  layouts with made-up content.
- CURR_TIME: the meshcore_py docs example, bytes 78 56 34 12 = 0x12345678.
- CONTACT: 148 bytes (MAX_PATH_SIZE 64, from meshcore_py reader.py), name at
  100-131, last_advert at 132-135.
- SEND_TXT: header 02 00, attempt byte, u32 LE timestamp, destination prefix,
  utf-8 text (meshcore_py commands/messaging.py send_msg, MyMesh.cpp parse).
No capture from a real node was used.
"""
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "meshcore_feed.py")


def load():
    sys.modules["bluetooth"] = types.ModuleType("bluetooth")
    badge = mock.MagicMock()
    badge.ticks = 0
    badge.pressed.return_value = False
    screen = mock.MagicMock()
    screen.measure_text.side_effect = lambda s: (len(s) * 6, 8)
    ns = {
        "badge": badge, "screen": screen, "font": mock.MagicMock(),
        "color": mock.MagicMock(), "run": lambda f: None,
        "HIRES": 1, "VSYNC": 2, "BUTTON_UP": "up", "BUTTON_DOWN": "down",
        "BUTTON_LEFT": "left", "BUTTON_RIGHT": "right",
        "BUTTON_SELECT": "sel", "BUTTON_BACK": "back", "__name__": "meshcore_app",
    }
    with open(APP) as f:
        exec(compile(f.read(), APP, "exec"), ns)
    return types.SimpleNamespace(**ns), ns


def contact_frame(prefix_byte=1, name=b"Sam", last=1000, path=64):
    f = bytes([3]) + bytes([prefix_byte]) * 32 + bytes([1, 0, 255]) + bytes(path)
    f += name.ljust(32, b"\x00") + last.to_bytes(4, "little") + bytes(12)
    return f


class Frames(unittest.TestCase):
    def setUp(self):
        self.m, self.ns = load()

    def test_builders(self):
        self.assertEqual(self.m.frame_device_query(), b"\x16\x03")
        self.assertEqual(self.m.frame_app_start(), b"\x01\x03" + bytes(6) + b"badge")
        self.assertEqual(self.m.frame_app_start()[8:], b"badge")  # MyMesh.cpp: name at offset 8

    def test_send_txt(self):
        f = self.m.frame_send_txt(b"\xaa\xbb\xcc\xdd\xee\xff", b"OK", 0x12345678)
        self.assertEqual(f, b"\x02\x00\x00\x78\x56\x34\x12\xaa\xbb\xcc\xdd\xee\xffOK")
        self.assertEqual(f[7:13], b"\xaa\xbb\xcc\xdd\xee\xff")  # prefix at 7, text at 13

    def test_time(self):
        self.assertEqual(self.m.u32(b"\x09\x78\x56\x34\x12", 1), 305419896)

    def test_contact_148(self):
        f = contact_frame(7, b"Hill rptr", 123456)
        self.assertEqual(len(f), 148)
        c = self.m.parse_contact(f)
        self.assertEqual(c, {"prefix": b"\x07" * 6, "name": "Hill rptr", "last": 123456})

    def test_contact_other_path_size(self):
        c = self.m.parse_contact(contact_frame(path=32))
        self.assertEqual((c["name"], c["last"]), ("Sam", 1000))

    def test_contact_short_or_bad(self):
        self.assertIsNone(self.m.parse_contact(b"\x03" + bytes(20)))
        c = self.m.parse_contact(contact_frame(name=b"\xff\xfe\xf0\x9f\x98\x80x"))
        self.assertIsInstance(c["name"], str)

    def test_dm_v3(self):
        f = b"\x10\x28\x00\x00" + b"\x01\x02\x03\x04\x05\x06" + b"\xff\x00" + (500).to_bytes(4, "little") + b"Hi there"
        self.assertEqual(self.m.parse_dm(f, True), (b"\x01\x02\x03\x04\x05\x06", "Hi there", 500))

    def test_dm_v2(self):
        f = b"\x07" + b"\x01\x02\x03\x04\x05\x06" + b"\xff\x00" + (500).to_bytes(4, "little") + b"Hi"
        self.assertEqual(self.m.parse_dm(f, False), (b"\x01\x02\x03\x04\x05\x06", "Hi", 500))

    def test_dm_signed_skips_signature(self):
        f = b"\x10\x00\x00\x00" + b"\x01" * 6 + b"\xff\x02" + bytes(4) + b"SIGN" + b"text"
        self.assertEqual(self.m.parse_dm(f, True)[1], "text")

    def test_channel_v3_and_v2(self):
        v3 = b"\x11\x00\x00\x00\x00\xff\x00" + (9).to_bytes(4, "little") + b"Pete: hello"
        self.assertEqual(self.m.parse_channel(v3, True), ("Pete: hello", 9))
        v2 = b"\x08\x00\xff\x00" + (9).to_bytes(4, "little") + b"Pete: hello"
        self.assertEqual(self.m.parse_channel(v2, False), ("Pete: hello", 9))

    def test_truncated_frames_do_not_raise(self):
        for n in range(0, 17):
            self.assertIsNone(self.m.parse_dm(b"\x10" * n, True))
        for n in range(0, 12):
            self.assertIsNone(self.m.parse_channel(b"\x11" * n, True))

    def test_text_cleanup(self):
        self.assertEqual(self.m.text_of(b"a\nb\tc"), "a b c")
        self.assertEqual(self.m.text_of("café \U0001f600!".encode()), "caf? ?!")
        self.assertEqual(self.m.text_of(b"half \xe2\x82"), "half ?")  # cut mid character
        self.assertEqual(self.m.text_of(b"\x80\x81 x"), "x")

    def test_adv_info(self):
        uuid = bytes(range(16))
        adv = b"\x02\x01\x06" + bytes([17, 7]) + uuid + b"\x0d\x09MeshCore-Sam"
        has, name = self.m.adv_info(adv, uuid)
        self.assertTrue(has)
        self.assertEqual(name, "MeshCore-Sam")
        self.assertEqual(self.m.adv_info(b"\x00", uuid), (False, ""))


class Wrapping(unittest.TestCase):
    def setUp(self):
        self.m, _ = load()

    def test_wrap_fits(self):
        for text in ["a b c " * 30, "x" * 200, "word " + "y" * 90 + " end"]:
            for line in self.m.wrap(text, 120):
                self.assertLessEqual(len(line) * 6, 120)

    def test_fit(self):
        self.assertEqual(self.m.fit("short", 100), "short")
        self.assertLessEqual(len(self.m.fit("x" * 80, 100)) * 6, 100)


class Safety(unittest.TestCase):
    """Reply needs three SELECT presses and a confirm screen. Nothing sends on one."""

    def setUp(self):
        self.m, self.ns = load()
        self.badge = self.ns["badge"]
        self.sent = []
        live = mock.MagicMock()
        live.send_reply.side_effect = lambda p, t: self.sent.append((p, t))
        live.step.return_value = None
        self.ns["live"] = live
        app = self.ns["app"]
        app.update(demo=False, screen="feed", sel=0, node_time=1000)
        self.ns["add_message"]("dm", b"\x01" * 6, "hello", 1)

    def press(self, *names):
        self.badge.pressed.side_effect = lambda b: b in names
        self.ns["handle_input"]()

    def test_select_flow(self):
        app = self.ns["app"]
        self.press("sel")
        self.assertEqual(app["screen"], "reply")
        self.assertEqual(self.sent, [])
        self.press("sel")
        self.assertEqual(app["screen"], "confirm")
        self.assertEqual(self.sent, [])
        self.badge.ticks = 100  # inside the guard time, stray press
        self.press("sel")
        self.assertEqual(self.sent, [])
        self.badge.ticks = 5000
        self.press("sel")
        self.assertEqual(self.sent, [(b"\x01" * 6, "OK")])

    def test_back_cancels(self):
        self.press("sel")
        self.press("sel")
        self.press("back")
        self.badge.ticks = 5000
        self.press("sel")
        self.assertEqual(self.sent, [])

    def test_target_fixed_when_new_message_arrives(self):
        app = self.ns["app"]
        self.press("sel")
        self.ns["add_message"]("dm", b"\x02" * 6, "other", 2)
        self.press("sel")
        self.badge.ticks = 5000
        self.press("sel")
        self.assertEqual(self.sent[0][0], b"\x01" * 6)

    def test_demo_never_sends(self):
        self.ns["app"]["demo"] = True
        self.press("sel")
        self.press("sel")
        self.badge.ticks = 5000
        self.press("sel")
        self.assertEqual(self.ns["app"]["screen"], "feed")
        self.assertEqual(self.sent, [])

    def test_canned_replies_are_short_plain(self):
        for t in self.ns["CANNED"]:
            self.assertTrue(t.isascii() and len(t) < 30)


class LiveGuard(unittest.TestCase):
    def test_no_gap_pair_stops_before_scanning(self):
        m, ns = load()

        class Ble:
            def active(self, v=None):
                return True

            def gap_scan(self, *a):
                raise AssertionError("must not scan")

        sys.modules["bluetooth"].BLE = Ble
        ns["start_live"]()
        self.assertIn("cannot pair", ns["app"]["status"])
        self.assertEqual(ns["app"]["screen"], "start")
        self.assertIsNone(ns["live"])

    def test_send_reply_refuses_when_not_ready(self):
        m, ns = load()
        live = ns["Live"].__new__(ns["Live"])
        live.state, live.mtu, live.queue, live.rx = "pair", 185, [], 1
        ns["app"]["node_time"] = 5
        live.send_reply(b"\x01" * 6, "OK")
        self.assertEqual(live.queue, [])

    def test_bad_frames_are_swallowed_and_counted(self):
        m, ns = load()
        live = ns["Live"].__new__(ns["Live"])
        live.state, live.bad_frames, live.events = "ready", 0, [(18, b"\x03" + b"\x00" * 3)]
        live.contact_buf, live.deadline = [], 0
        live.step()
        self.assertEqual(live.events, [])


if __name__ == "__main__":
    unittest.main()
