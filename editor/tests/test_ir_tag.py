"""Host tests for the ir_tag app. Run: python3 -I editor/tests/test_ir_tag.py

The app is exec'd with stand-in badge globals and a fake aye_arr, so these
check the game logic and how the app calls the library. Nothing here proves
anything about the real IR hardware or the real aye_arr build on the badge.
"""
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "ir_tag.py")
UID = bytes([0xE6, 0x61, 0x64, 0x08, 0x43, 0x12, 0x34, 0x56])


class FakeDescriptor:
    def __init__(self):
        self.on_known = None
        self.on_any = None


class FakeReceiver:
    instances = []

    def __init__(self, pin, pio, sm, *args):
        self.args = (pin, pio, sm)
        self.remote = None
        self.started = False
        FakeReceiver.instances.append(self)

    def bind(self, remote):
        self.remote = remote

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def decode(self):
        pass


class FakeSender:
    fail_on = ()           # (pio, sm) pairs that raise, like aye_arr 1.0.0 on a shared PIO
    instances = []

    def __init__(self, pin, pio, sm):
        if (pio, sm) in FakeSender.fail_on:
            raise ValueError("PIO busy")
        self.args = (pin, pio, sm)
        self.started = False
        self.sent = []
        FakeSender.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def send_addr_cmd(self, addr, cmd):
        if not self.started:
            raise RuntimeError("not started")
        self.sent.append((addr, cmd))


def fake_aye_arr(receiver_name="NECRemoteReceiver", sender=True):
    mods = {}
    for name in ("aye_arr", "aye_arr.nec", "aye_arr.nec.remotes",
                 "aye_arr.nec.remotes.descriptor"):
        mods[name] = types.ModuleType(name)
    mods["aye_arr.nec.remotes.descriptor"].RemoteDescriptor = FakeDescriptor
    setattr(mods["aye_arr.nec"], receiver_name, FakeReceiver)
    if sender:
        mods["aye_arr.nec"].NECSender = FakeSender
    return mods


def load(ir=None, uid=UID, saved=None):
    """Exec the app. ir is None for the simulator case, else a dict of fake modules."""
    for name in list(sys.modules):
        if name.startswith("aye_arr"):
            del sys.modules[name]
    if ir:
        sys.modules.update(ir)
    FakeReceiver.instances.clear()
    FakeSender.instances.clear()

    machine = types.ModuleType("machine")
    machine.unique_id = lambda: uid
    sys.modules["machine"] = machine

    state = types.ModuleType("badgeware")

    class State:
        saves = []

        @staticmethod
        def load(name, d):
            d.update(saved or {})

        @staticmethod
        def save(name, d):
            State.saves.append(dict(d))

    state.State = State
    sys.modules["badgeware"] = state

    badge = mock.MagicMock()
    badge.ticks = 10000
    held = set()
    badge.pressed.side_effect = lambda b: b in held
    screen = mock.MagicMock()
    screen.width, screen.height = 160, 120
    ns = {
        "badge": badge, "screen": screen, "font": mock.MagicMock(),
        "color": mock.MagicMock(), "run": lambda f: None, "reset": mock.MagicMock(),
        "BUTTON_SELECT": "sel", "BUTTON_UP": "up", "BUTTON_BACK": "back",
        "__name__": "ir_tag_app",
    }
    with open(APP) as f:
        exec(compile(f.read(), APP, "exec"), ns)
    ns["held"], ns["State"] = held, State
    return ns


def press(app, button):
    app["held"].add(button)
    app["update"]()
    app["held"].discard(button)


def advance(app, ms):
    app["badge"].ticks += ms
    app["update"]()


class Ids(unittest.TestCase):
    def test_range_for_many_uids(self):
        for a in range(256):
            for b in range(0, 256, 17):
                app = load(uid=bytes([a, b, 1, 2, 3, 4, 5, 6]))
                self.assertTrue(1 <= app["my_id"] <= 120)

    def test_ack_code_fits_in_a_byte(self):
        self.assertEqual(120 + 128, 248)
        self.assertLess(248, 256)

    def test_later_bytes_change_the_id(self):
        ids = {load(uid=bytes([0xE6, 0x61, 0, 0, 0, 0, 0, n]))["my_id"] for n in range(60)}
        self.assertGreater(len(ids), 20)


class Simulator(unittest.TestCase):
    def setUp(self):
        self.app = load()

    def test_no_ir_modules_means_sim(self):
        self.assertFalse(self.app["HAVE_IR"])
        self.assertIsNone(self.app["sender"])

    def test_shot_is_confirmed_once(self):
        a = self.app
        press(a, "sel")
        self.assertEqual(a["last_event"], "fired")
        advance(a, 200)
        self.assertEqual(a["stats"]["tags"], 1)
        self.assertEqual(a["last_event"], "hit confirmed")
        self.assertEqual(a["streak"], 1)

    def test_ack_without_a_shot_is_ignored(self):
        a = self.app
        a["on_code"](a["my_id"] + 128)
        self.assertEqual(a["stats"]["tags"], 0)

    def test_two_acks_for_one_shot_count_once(self):
        a = self.app
        press(a, "sel")
        a["on_code"](a["my_id"] + 128)
        a["on_code"](a["my_id"] + 128)
        self.assertEqual(a["stats"]["tags"], 1)

    def test_late_ack_is_ignored(self):
        a = self.app
        a["fire"](a["badge"].ticks)
        a["badge"].ticks += 1500
        a["on_code"](a["my_id"] + 128)
        self.assertEqual(a["stats"]["tags"], 0)

    def test_miss_clears_streak(self):
        a = self.app
        a["streak"] = 3
        a["fire"](a["badge"].ticks)
        a["sim_ack_at"] = None            # no confirmation arrives
        advance(a, 1100)
        self.assertEqual(a["last_event"], "missed")
        self.assertEqual(a["streak"], 0)

    def test_cooldown_blocks_second_shot(self):
        a = self.app
        press(a, "sel")
        first = a["last_shot"]
        advance(a, 100)
        press(a, "sel")
        self.assertEqual(a["last_shot"], first)
        advance(a, 400)
        press(a, "sel")
        self.assertGreater(a["last_shot"], first)

    def test_up_fakes_a_hit_from_7(self):
        a = self.app
        press(a, "up")
        self.assertEqual(a["stats"]["hits"], 1)
        self.assertEqual(a["last_event"], "tagged by #7")

    def test_up_does_not_crash_when_id_is_7(self):
        a = load(uid=bytes([0]))
        a["my_id"] = 7
        press(a, "up")
        self.assertEqual(a["stats"]["hits"], 1)


class Saving(unittest.TestCase):
    def test_hits_are_saved_after_a_quiet_period_not_on_every_hit(self):
        a = load()
        for _ in range(5):
            a["on_code"](9)
            advance(a, 100)
        self.assertEqual(a["State"].saves, [])
        advance(a, 2100)
        self.assertEqual(len(a["State"].saves), 1)
        self.assertEqual(a["State"].saves[0]["hits"], 5)

    def test_back_saves_before_leaving(self):
        a = load()
        a["on_code"](9)
        press(a, "back")
        self.assertEqual(len(a["State"].saves), 1)
        a["reset"].assert_called_once()

    def test_on_exit_saves_and_stops(self):
        a = load(ir=fake_aye_arr())
        a["on_code"](9)
        a["on_exit"]()
        self.assertEqual(len(a["State"].saves), 1)
        self.assertFalse(a["receiver"].started)
        self.assertFalse(a["sender"].started)

    def test_saved_stats_are_loaded(self):
        a = load(saved={"tags": 4, "hits": 2})
        self.assertEqual((a["stats"]["tags"], a["stats"]["hits"]), (4, 2))


class WithIr(unittest.TestCase):
    def test_uses_remote_receiver_when_available(self):
        a = load(ir=fake_aye_arr("NECRemoteReceiver"))
        self.assertTrue(a["HAVE_IR"])
        self.assertEqual(a["receiver"].args, (17, 0, 0))
        self.assertTrue(a["receiver"].started)
        self.assertIs(a["receiver"].remote, a["remote"])

    def test_falls_back_to_2025_receiver_name(self):
        a = load(ir=fake_aye_arr("NECReceiver"))
        self.assertTrue(a["HAVE_IR"])
        self.assertIsNotNone(a["receiver"])

    def test_sender_args_and_start(self):
        a = load(ir=fake_aye_arr())
        self.assertEqual(a["sender"].args, (16, 0, 1))
        self.assertTrue(a["sender"].started)

    def test_sender_falls_back_to_pio1(self):
        FakeSender.fail_on = ((0, 1),)
        try:
            a = load(ir=fake_aye_arr())
        finally:
            FakeSender.fail_on = ()
        self.assertEqual(a["sender"].args, (16, 1, 0))

    def test_no_sender_is_receive_only(self):
        a = load(ir=fake_aye_arr(sender=False))
        self.assertIsNone(a["sender"])
        self.assertTrue(a["status"].startswith("rx only"))
        press(a, "sel")
        self.assertEqual(a["last_event"], "send failed")
        self.assertEqual(a["ack_until"], 0)

    def test_every_sender_failing_is_reported(self):
        FakeSender.fail_on = ((0, 1), (1, 0))
        try:
            a = load(ir=fake_aye_arr())
        finally:
            FakeSender.fail_on = ()
        self.assertIsNone(a["sender"])
        self.assertIn("PIO busy", a["status"])

    def test_address_differs_from_quest_and_codes_cover_protocol(self):
        a = load(ir=fake_aye_arr())
        remote = a["TagRemote"]
        self.assertEqual(remote.ADDRESS, 0x47)
        self.assertNotEqual(remote.ADDRESS, 0x45)
        for code in list(range(1, 121)) + list(range(129, 249)):
            self.assertIn(code, remote.BUTTON_CODES)
            self.assertEqual(remote.BUTTON_CODES[code], code)

    def test_fire_sends_my_id(self):
        a = load(ir=fake_aye_arr())
        press(a, "sel")
        self.assertEqual(a["sender"].sent, [(0x47, a["my_id"])])

    def test_hit_sends_ack_after_delay(self):
        a = load(ir=fake_aye_arr())
        a["remote"].on_known(7)
        self.assertEqual(a["sender"].sent, [])
        advance(a, 50)
        self.assertEqual(a["sender"].sent, [])
        advance(a, 100)
        self.assertEqual(a["sender"].sent, [(0x47, 7 + 128)])

    def test_callback_accepts_extra_args_from_new_library(self):
        a = load(ir=fake_aye_arr())
        a["remote"].on_known(7, 12345, 12000)
        self.assertEqual(a["stats"]["hits"], 1)

    def test_own_reflection_is_ignored_only_right_after_firing(self):
        a = load(ir=fake_aye_arr())
        press(a, "sel")
        a["remote"].on_known(a["my_id"])
        self.assertEqual(a["stats"]["hits"], 0)
        advance(a, 600)
        a["remote"].on_known(a["my_id"])
        self.assertEqual(a["stats"]["hits"], 1)

    def test_own_ack_heard_back_does_nothing(self):
        a = load(ir=fake_aye_arr())
        a["remote"].on_known(7)
        advance(a, 150)
        a["remote"].on_known(7 + 128)      # our ack bouncing into our receiver
        self.assertEqual(a["stats"]["tags"], 0)

    def test_full_exchange_between_two_badges(self):
        shooter = load(ir=fake_aye_arr(), uid=bytes([1, 2, 3, 4, 5, 6, 7, 8]))
        target = load(ir=fake_aye_arr(), uid=bytes([9, 9, 9, 9, 9, 9, 9, 9]))
        self.assertNotEqual(shooter["my_id"], target["my_id"])
        press(shooter, "sel")
        _, cmd = shooter["sender"].sent[0]
        target["remote"].on_known(cmd)
        advance(target, 150)
        _, ack = target["sender"].sent[0]
        advance(shooter, 200)
        shooter["remote"].on_known(ack)
        self.assertEqual(target["stats"]["hits"], 1)
        self.assertEqual(shooter["stats"]["tags"], 1)

    def test_receiver_failure_is_shown_not_fatal(self):
        class Bad(FakeReceiver):
            def __init__(self, *a):
                raise ValueError("pio taken")
        mods = fake_aye_arr()
        mods["aye_arr.nec"].NECRemoteReceiver = Bad
        a = load(ir=mods)
        self.assertIsNone(a["receiver"])
        a["update"]()


if __name__ == "__main__":
    unittest.main()
