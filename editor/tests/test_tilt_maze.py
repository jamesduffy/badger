"""Host tests for the tilt_maze app. Run: python3 -I editor/tests/test_tilt_maze.py

The app is exec'd with stand-in badge globals and a fake IMU. The Level class
is driven with scripted tilt, so this checks maze generation and ball physics
only. It says nothing about how the real IMU axes or signs behave.
"""
import os
import random
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "tilt_maze.py")
BADGE_APP = os.path.join(HERE, "..", "..", "badge", "apps", "tilt_maze", "__init__.py")


class Vec:
    def __init__(self, x=0.0, y=0.0):
        self.x = x
        self.y = y


def load(imu=(0, 0, 16384, 0, 0, 0), upside_down=False, push=(0.0, 0.0)):
    saved = {}
    mod = types.ModuleType("badgeware")
    mod.State = mock.MagicMock()
    mod.State.save.side_effect = lambda app, data: saved.update(data)
    sys.modules["badgeware"] = mod
    badge = mock.MagicMock()
    badge.ticks_delta = 33
    badge.pressed.return_value = False
    badge.imu.side_effect = lambda: imu
    badge.upside_down.side_effect = lambda: upside_down
    badge.direction.side_effect = lambda: Vec(*push)
    screen = mock.MagicMock()
    screen.width = 160
    screen.height = 120
    screen.measure_text.side_effect = lambda s: (len(s) * 6, 8)
    ns = {
        "badge": badge, "screen": screen, "font": mock.MagicMock(),
        "color": mock.MagicMock(), "shape": mock.MagicMock(),
        "run": lambda f: None, "reset": mock.MagicMock(),
        "BUTTON_SELECT": "sel", "BUTTON_BACK": "back",
        "__name__": "tilt_maze_app",
    }
    with open(APP) as f:
        exec(compile(f.read(), APP, "exec"), ns)
    ns["saved"] = saved
    return ns


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def path_tiles(app, level):
    """Shortest tile path from the start to the goal (BFS)."""
    grid = level.grid
    start = (1, 1)
    prev = {start: None}
    queue = [start]
    i = 0
    while i < len(queue):
        x, y = queue[i]
        i += 1
        for dx, dy in app["NEIGHBOURS"]:
            t = (x + dx, y + dy)
            if not grid[t[1]][t[0]] and t not in prev:
                prev[t] = (x, y)
                queue.append(t)
    if level.goal not in prev:
        return None
    path = []
    t = level.goal
    while t:
        path.append(t)
        t = prev[t]
    return path[::-1]


def autopilot(app, level, dt, max_seconds=120.0, check_walls=True):
    """Steer along the BFS path with a simple spring and damper on tilt."""
    path = path_tiles(app, level)
    assert path, "goal not reachable"
    wp = 1
    t = level.tile
    while level.elapsed < max_seconds:
        if level.at_goal():
            return True
        # advance when close to the waypoint centre, but never skip the last tile
        tx, ty = path[wp]
        cx, cy = (tx + 0.5) * t, (ty + 0.5) * t
        while wp < len(path) - 1 and abs(level.x - cx) < t * 0.35 and abs(level.y - cy) < t * 0.35:
            wp += 1
            tx, ty = path[wp]
            cx, cy = (tx + 0.5) * t, (ty + 0.5) * t
        # aim at the waypoint, but hold the other axis on the corridor centre line
        px, py = path[wp - 1]
        horizontal = ty == py
        ex = cx - level.x
        ey = cy - level.y
        kp, kd = 0.12, 0.06
        gx = clamp(kp * ex - kd * level.vx, -1.0, 1.0)
        gy = clamp(kp * ey - kd * level.vy, -1.0, 1.0)
        level.step(dt, gx, gy)
        if check_walls:
            assert not level.blocked(level.x, level.y), "ball inside a wall"
        assert horizontal in (True, False)
    return False


class Maze(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load()

    def test_every_size_is_connected_and_goal_is_far(self):
        app = self.app
        for idx in range(len(app["LEVELS"]) + 3):
            for seed in range(60):
                random.seed(seed)
                lv = app["Level"](idx)
                self.assertFalse(lv.grid[1][1])
                gx, gy = lv.goal
                self.assertFalse(lv.grid[gy][gx])
                self.assertNotEqual(lv.goal, (1, 1))
                self.assertIsNotNone(path_tiles(app, lv), (idx, seed))
                # border is solid so the ball can never index outside the grid
                self.assertTrue(all(lv.grid[0]) and all(lv.grid[-1]))
                self.assertTrue(all(r[0] and r[-1] for r in lv.grid))

    def test_fits_the_screen(self):
        app = self.app
        for idx in range(len(app["LEVELS"])):
            lv = app["Level"](idx)
            self.assertGreaterEqual(lv.ox, 0)
            self.assertLessEqual(lv.ox + lv.cols * lv.tile, 160)
            self.assertLessEqual(lv.oy + lv.rows * lv.tile, 120)
            self.assertGreaterEqual(lv.oy, app["HUD_H"])

    def test_start_is_not_blocked(self):
        app = self.app
        for idx in range(len(app["LEVELS"])):
            lv = app["Level"](idx)
            self.assertFalse(lv.blocked(lv.x, lv.y))

    def test_level_past_the_last_size_still_builds(self):
        app = self.app
        lv = app["Level"](len(app["LEVELS"]) + 10)
        self.assertEqual((lv.cols, lv.rows), app["LEVELS"][-1])


class Physics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load()

    def test_autopilot_reaches_goal_at_several_frame_rates(self):
        app = self.app
        for idx in range(len(app["LEVELS"])):
            for dt in (1 / 60, 1 / 30, app["MAX_DT"]):
                for seed in range(15):
                    random.seed(seed * 7 + idx)
                    lv = app["Level"](idx)
                    self.assertTrue(autopilot(app, lv, dt), (idx, dt, seed, lv.elapsed))

    def test_random_full_tilt_never_enters_a_wall(self):
        app = self.app
        rng = random.Random(1)
        for idx in range(len(app["LEVELS"])):
            for seed in range(10):
                random.seed(seed)
                lv = app["Level"](idx)
                gx = gy = 0.0
                for n in range(4000):
                    if n % 40 == 0:
                        gx = rng.choice((-1.0, 0.0, 1.0))
                        gy = rng.choice((-1.0, 0.0, 1.0))
                    lv.step(rng.choice((1 / 60, 1 / 30, app["MAX_DT"])), gx, gy)
                    self.assertFalse(lv.blocked(lv.x, lv.y), (idx, seed, n))
                    self.assertLessEqual((lv.vx ** 2 + lv.vy ** 2) ** 0.5, app["MAX_SPEED"] * 1.001)

    def test_hard_push_into_a_wall_does_not_tunnel(self):
        app = self.app
        for idx in range(len(app["LEVELS"])):
            random.seed(3)
            lv = app["Level"](idx)
            for gx, gy in ((1, 0), (0, 1), (1, 1), (-1, -1)):
                for _ in range(600):
                    lv.step(app["MAX_DT"], gx, gy)
                    self.assertFalse(lv.blocked(lv.x, lv.y))

    def test_corner_slide_in_a_corridor(self):
        """Pushing diagonally into a corridor wall should slide along it."""
        app = self.app
        random.seed(5)
        lv = app["Level"](0)
        # start tile (1,1): open to the right or down. Push down-right and
        # check the ball keeps making progress along the open axis.
        open_right = not lv.grid[1][2]
        start = lv.x if open_right else lv.y
        for _ in range(60):
            lv.step(1 / 30, 1.0, 1.0)
        moved = (lv.x if open_right else lv.y) - start
        self.assertGreater(moved, lv.tile * 0.5)

    def test_same_motion_at_different_frame_rates(self):
        """One second of free rolling should cover about the same distance."""
        app = self.app
        ends = []
        for dt in (1 / 60, 1 / 30, app["MAX_DT"]):
            random.seed(2)
            lv = app["Level"](0)
            for _ in range(round(0.5 / dt)):
                lv.step(dt, 0.0, 0.0)
                lv.vx = 0.0
            lv.vx = lv.vy = 0.0
            for _ in range(round(0.4 / dt)):
                lv.step(dt, 1.0, 0.0)
            ends.append(lv.vx)
        self.assertLess(max(ends) - min(ends), 0.1 * max(ends))


class Tilt(unittest.TestCase):
    def test_flat_gives_no_motion_after_first_read(self):
        app = load(imu=(100, 50, 16384, 0, 0, 0))
        t = app["Tilt"]()
        self.assertEqual(t.read(), (0.0, 0.0))

    def test_sign_and_scale(self):
        imu = [0, 0, 16384, 0, 0, 0]
        app = load()
        app["badge"].imu.side_effect = lambda: tuple(imu)
        t = app["Tilt"]()
        t.read()
        imu[0] = 4096  # a quarter g on ax
        x, y = t.read()
        self.assertLess(x, 0)
        self.assertAlmostEqual(x, -0.5, places=2)
        imu[0] = 0
        imu[2] = 16384 + 4096
        x, y = t.read()
        self.assertGreater(y, 0)
        imu[2] = 16384 + 40000
        self.assertEqual(t.read()[1], 1.0)

    def test_recentre_takes_current_pose_as_neutral(self):
        imu = [0, 0, 16384, 0, 0, 0]
        app = load()
        app["badge"].imu.side_effect = lambda: tuple(imu)
        t = app["Tilt"]()
        t.read()
        imu[0] = 6000
        self.assertNotEqual(t.read()[0], 0)
        t.recentre()
        self.assertEqual(t.read(), (0.0, 0.0))

    def test_upside_down_flips_both_axes(self):
        results = []
        for flip in (False, True):
            imu = [0, 0, 16384, 0, 0, 0]
            app = load(upside_down=flip)
            app["badge"].imu.side_effect = lambda: tuple(imu)
            t = app["Tilt"]()
            t.read()
            imu[0], imu[2] = 3000, 16384 + 3000
            results.append(t.read())
        self.assertAlmostEqual(results[0][0], -results[1][0], places=3)
        self.assertAlmostEqual(results[0][1], -results[1][1], places=3)
        self.assertNotEqual(results[0][0], 0)

    def test_flip_recentres_so_the_old_rest_pose_is_not_reused(self):
        imu = [0, 0, 16384, 0, 0, 0]
        flip = [False]
        app = load()
        app["badge"].imu.side_effect = lambda: tuple(imu)
        app["badge"].upside_down.side_effect = lambda: flip[0]
        t = app["Tilt"]()
        t.read()
        flip[0] = True
        imu[0] = -200  # same pose seen through a flipped sensor frame
        self.assertEqual(t.read(), (0.0, 0.0))

    def test_deadzone_ignores_sensor_noise(self):
        imu = [0, 0, 16384, 0, 0, 0]
        app = load()
        app["badge"].imu.side_effect = lambda: tuple(imu)
        t = app["Tilt"]()
        t.read()
        imu[0], imu[2] = 300, 16384 - 300  # about 1 degree
        self.assertEqual(t.read(), (0.0, 0.0))

    def test_dpad_adds_and_result_is_clamped(self):
        app = load(push=(1.0, -1.0))
        t = app["Tilt"]()
        self.assertEqual(t.read(), (1.0, -1.0))


class Game(unittest.TestCase):
    def play(self, app, frames, select_on=()):
        sel = set(select_on)
        n = [0]
        badge = app["badge"]

        def pressed(b):
            return b == "sel" and n[0] in sel

        badge.pressed.side_effect = pressed
        for i in range(frames):
            n[0] = i
            app["update"]()

    def test_frames_run_and_win_saves_a_best_time(self):
        app = load()
        random.seed(4)
        app["level"] = app["Level"](0)
        lv = app["level"]
        lv.ready = 0
        lv.x, lv.y = (lv.goal[0] + 0.5) * lv.tile, (lv.goal[1] + 0.5) * lv.tile
        self.play(app, 3)
        self.assertEqual(app["mode"], app["WIN"])
        self.assertGreater(app["saved"]["best_0"], 0)

    def test_countdown_blocks_movement_then_starts(self):
        app = load(push=(1.0, 0.0))
        lv = app["level"]
        x0 = lv.x
        self.play(app, 30)  # about 1 s at 33 ms a frame
        self.assertEqual(lv.x, x0)
        self.assertEqual(lv.elapsed, 0.0)
        self.play(app, 60)
        self.assertGreater(lv.x, x0)
        self.assertGreater(lv.elapsed, 0.5)

    def test_select_skips_the_countdown(self):
        app = load()
        self.play(app, 2, select_on=(1,))
        self.assertEqual(app["level"].ready, 0)

    def test_win_lights_the_case_and_back_turns_them_off(self):
        app = load()
        lv = app["level"]
        lv.ready = 0
        lv.x, lv.y = (lv.goal[0] + 0.5) * lv.tile, (lv.goal[1] + 0.5) * lv.tile
        self.play(app, 5)
        levels = [c.args[0] for c in app["badge"].caselights.call_args_list]
        self.assertGreater(max(levels), 0.5)
        self.assertLess(levels[-1], max(levels))
        app["badge"].pressed.side_effect = lambda b: b == "back"
        app["update"]()
        self.assertEqual(app["badge"].caselights.call_args.args[0], 0)

    def test_select_after_win_builds_the_next_level_and_never_crashes(self):
        app = load()
        for _ in range(8):
            lv = app["level"]
            lv.ready = 0
            lv.x, lv.y = (lv.goal[0] + 0.5) * lv.tile, (lv.goal[1] + 0.5) * lv.tile
            self.play(app, 2)
            self.assertEqual(app["mode"], app["WIN"])
            before = app["level_index"]
            self.play(app, 3, select_on=(1,))
            self.assertEqual(app["level_index"], before + 1)
            self.assertEqual(app["mode"], app["PLAY"])

    def test_back_resets(self):
        app = load()
        app["badge"].pressed.side_effect = lambda b: b == "back"
        app["update"]()
        app["reset"].assert_called()


class Sync(unittest.TestCase):
    def test_editor_copy_matches_badge_app(self):
        with open(APP) as a, open(BADGE_APP) as b:
            self.assertEqual(a.read(), b.read())


if __name__ == "__main__":
    unittest.main()
