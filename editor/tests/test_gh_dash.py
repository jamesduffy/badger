"""Host tests for the gh_dash app. Run: python3 -I editor/tests/test_gh_dash.py

Sample JSON under tests/data (gh_dash_search.json, gh_dash_runs.json) is shaped
like the examples in the GitHub REST docs (hand trimmed, not captured from the
live API). `requests` and `wifi` are stubs and the app is exec'd with stand-in
badge globals, so nothing here proves anything about the real API or hardware.
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
APP = os.path.join(HERE, "..", "gh_dash.py")
TOKEN = "github_pat_SECRET0123456789abcdef"


def sample(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)


class Module:
    pass


class Response:
    def __init__(self, code=200, body=None, raw_error=None):
        self.status_code = code
        self.body = body
        self.raw_error = raw_error
        self.closed = False

    def json(self):
        if self.raw_error:
            raise self.raw_error
        return self.body

    def close(self):
        self.closed = True


class Net:
    """Stub requests. routes: list of (url fragment, Response or Exception)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        for fragment, reply in self.routes:
            if fragment in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise AssertionError("no route for " + url)


def ok_routes(runs=None):
    search = sample("gh_dash_search.json")
    return [
        ("search/issues", Response(200, search)),
        ("actions/runs", Response(200, runs or sample("gh_dash_runs.json"))),
    ]


class App:
    """The app exec'd with stubs. Drawn text is collected from screen.text."""

    def __init__(self, routes, repos="octo/api,octo/web", user="octocat", token=TOKEN, wifi_up=True):
        self.net = Net(routes)
        sec = Module()
        for k, v in {"GITHUB_USERNAME": user, "GITHUB_TOKEN": token, "DASH_REPOS": repos}.items():
            setattr(sec, k, v)
        wifi = Module()
        wifi.is_connected = lambda: wifi_up
        wifi.connect = lambda *a, **k: wifi_up
        wifi.status = lambda: (-1, "bad password")
        sys.modules["requests"] = self.net
        sys.modules["secrets"] = sec
        sys.modules["wifi"] = wifi
        self.badge = mock.MagicMock()
        self.badge.ticks = 0
        self.badge.pressed.return_value = False
        self.pressed = set()
        self.badge.pressed.side_effect = lambda b: b in self.pressed
        self.drawn = []
        self.screen = mock.MagicMock()
        self.screen.width, self.screen.height = 320, 240
        self.screen.measure_text.side_effect = lambda s: (len(s) * 6, 10)
        self.screen.text.side_effect = lambda s, *a: self.drawn.append(s)
        ns = {
            "badge": self.badge, "screen": self.screen, "font": mock.MagicMock(),
            "image": mock.MagicMock(), "color": mock.MagicMock(),
            "shape": mock.MagicMock(), "run": lambda f: None, "reset": mock.MagicMock(),
            "HIRES": 1, "VSYNC": 2, "BUTTON_SELECT": "sel", "BUTTON_BACK": "back",
        }
        with open(APP) as f:
            exec(compile(f.read(), APP, "exec"), ns)
        self.ns = ns

    def frame(self, ticks=None):
        if ticks is not None:
            self.badge.ticks = ticks
        self.drawn = []
        self.ns["update"]()
        return list(self.drawn)

    def run_pass(self, start=0):
        """Frames until the pass stops asking the network for things."""
        t = start
        for _ in range(40):
            self.frame(t)
            t += 100
        return t

    def values(self):
        return {i.name: i.value for i in self.ns["items"]}

    def texts(self):
        return " | ".join(self.drawn)


class Success(unittest.TestCase):
    def test_counts_and_runs(self):
        app = App(ok_routes())
        app.run_pass()
        self.assertEqual(app.values(), {"reviews": 3, "prs": 3, "issues": 3, "ci0": "success", "ci1": "success"})
        self.assertIn("pass", app.texts())
        self.assertEqual(len(app.net.calls), 5)

    def test_headers_and_urls(self):
        app = App(ok_routes())
        app.run_pass()
        urls = [u for u, _ in app.net.calls]
        self.assertIn("/search/issues?q=is:pr+is:open+review-requested:octocat&per_page=1", urls[0])
        self.assertIn("is:pr+is:open+author:octocat", urls[1])
        self.assertIn("is:issue+is:open+assignee:octocat", urls[2])
        self.assertTrue(urls[3].endswith("/repos/octo/api/actions/runs?per_page=1&status=completed&exclude_pull_requests=true"))
        h = app.net.calls[0][1]
        self.assertEqual(h["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(h["Accept"], "application/vnd.github+json")
        self.assertEqual(h["X-GitHub-Api-Version"], "2022-11-28")
        self.assertTrue(h["User-Agent"])

    def test_branch_suffix(self):
        app = App(ok_routes(), repos="octo/api@release/1.x")
        app.run_pass()
        self.assertTrue(app.net.calls[3][0].endswith("&branch=release/1.x"))

    def test_updating_frame_comes_before_the_blocking_request(self):
        app = App(ok_routes())
        app.frame(0)
        self.assertEqual(app.net.calls, [])
        self.assertIn("updating...", app.texts())
        app.frame(100)
        self.assertEqual(len(app.net.calls), 1)

    def test_responses_closed(self):
        routes = ok_routes()
        app = App(routes)
        app.run_pass()
        self.assertTrue(all(r.closed for _, r in routes))

    def test_next_pass_after_refresh_interval(self):
        app = App(ok_routes())
        t = app.run_pass()
        n = len(app.net.calls)
        app.frame(t + 100_000)
        app.frame(t + 100_100)
        self.assertEqual(len(app.net.calls), n)
        app.frame(t + 400_000)
        app.frame(t + 400_100)
        self.assertEqual(len(app.net.calls), n + 1)

    def test_select_restarts_the_pass(self):
        app = App(ok_routes())
        t = app.run_pass()
        n = len(app.net.calls)
        app.pressed = {"sel"}
        app.frame(t + 10)
        app.pressed = set()
        app.frame(t + 20)
        self.assertEqual(len(app.net.calls), n + 1)
        self.assertEqual(app.net.calls[-1][0].split("?")[0].split("/")[-1], "issues")
        self.assertIn("review-requested", app.net.calls[-1][0])

    def test_run_conclusions(self):
        for conclusion, text in [("cancelled", "cancelled"), ("skipped", "skipped"), ("neutral", "neutral"),
                                 ("stale", "stale"), ("failure", "fail"), ("timed_out", "fail"),
                                 ("action_required", "needs approval")]:
            runs = {"total_count": 1, "workflow_runs": [{"status": "completed", "conclusion": conclusion}]}
            app = App(ok_routes(runs), repos="octo/api")
            app.run_pass()
            self.assertIn(text, app.texts(), conclusion)

    def test_empty_runs(self):
        app = App(ok_routes({"total_count": 0, "workflow_runs": []}), repos="octo/api")
        app.run_pass()
        self.assertEqual(app.values()["ci0"], "none")
        self.assertIn("no runs", app.texts())


class Failures(unittest.TestCase):
    def search_route(self, code, body):
        return [("search/issues", Response(code, body)), ("actions/runs", Response(200, sample("gh_dash_runs.json")))]

    def test_401(self):
        app = App(self.search_route(401, {"message": "Bad credentials"}))
        app.run_pass()
        self.assertIn("token rejected (401)", app.texts())
        self.assertEqual(len(app.net.calls), 1)  # pass stops, no hammering

    def test_403_rate_limit(self):
        body = {"message": "API rate limit exceeded for user ID 1.", "documentation_url": "x"}
        app = App(self.search_route(403, body))
        t = app.run_pass()
        self.assertIn("rate limited", app.texts())
        self.assertEqual(len(app.net.calls), 1)
        app.frame(t + 30_000)
        app.frame(t + 30_100)
        self.assertEqual(len(app.net.calls), 1)  # still waiting
        app.frame(t + 70_000)
        app.frame(t + 70_100)
        self.assertEqual(len(app.net.calls), 2)

    def test_403_no_access(self):
        app = App(self.search_route(403, {"message": "Resource not accessible by personal access token"}))
        app.run_pass()
        self.assertIn("Resource not accessible", app.texts())

    def test_422_keeps_going(self):
        app = App(self.search_route(422, {"message": "Validation Failed"}))
        app.run_pass()
        self.assertIn("search rejected", app.texts())
        self.assertEqual(app.values()["ci0"], "success")

    def test_404_repo_does_not_block_the_next_repo(self):
        routes = [
            ("search/issues", Response(200, sample("gh_dash_search.json"))),
            ("octo/api/", Response(404, {"message": "Not Found"})),
            ("octo/web/", Response(200, sample("gh_dash_runs.json"))),
        ]
        app = App(routes)
        app.run_pass()
        self.assertEqual(app.values()["ci1"], "success")
        self.assertIsNone(app.values()["ci0"])
        self.assertIn("api: not found", app.texts())
        self.assertIn("error", app.texts())

    def test_malformed_json(self):
        routes = [("search/issues", Response(200, raw_error=ValueError("syntax error in JSON"))),
                  ("actions/runs", Response(200, sample("gh_dash_runs.json")))]
        app = App(routes)
        app.run_pass()
        self.assertIn("syntax error in JSON", app.texts())

    def test_wrong_shape(self):
        routes = [("search/issues", Response(200, {"nope": 1})), ("actions/runs", Response(200, {"x": []}))]
        app = App(routes)
        app.run_pass()  # KeyError must not crash the frame loop
        self.assertIn("total_count", app.texts())

    def test_null_body(self):
        routes = [("search/issues", Response(200, None)), ("actions/runs", Response(200, None))]
        App(routes).run_pass()

    def test_timeout_and_memory(self):
        for exc in (OSError("timed out"), MemoryError()):
            app = App([("search/issues", exc), ("actions/runs", exc)])
            app.run_pass()
            self.assertEqual(len(app.net.calls), 1)  # a dead network is not asked 5 times
            self.assertTrue(app.texts())

    def test_stale_value_shows_age(self):
        app = App(ok_routes())
        t = app.run_pass()
        app.ns["counts"][0].error = "x"
        app.ns["feed"].next_at = 10 ** 9
        out = app.frame(t + 12 * 60000)
        self.assertIn("3 12m old", out)

    def test_wifi_down(self):
        app = App(ok_routes(), wifi_up=False)
        app.run_pass()
        self.assertEqual(app.net.calls, [])
        self.assertIn("bad password", app.texts())

    def test_5xx(self):
        app = App(self.search_route(502, {"message": "Server Error"}))
        app.run_pass()
        self.assertIn("HTTP 502", app.texts())
        self.assertEqual(len(app.net.calls), 1)


class Config(unittest.TestCase):
    def test_demo_without_token(self):
        app = App([], token="")
        out = app.frame(0)
        self.assertEqual(app.net.calls, [])
        self.assertIn("demo data, set GITHUB_USERNAME and GITHUB_TOKEN", out)
        self.assertIn("cancelled", out)

    def test_bad_repo_entries_dropped(self):
        app = App(ok_routes(), repos="nodash, a/b/c, ok/one, ../../x/y, a/b?x=1, good/two, third/3, fourth/4")
        names = [i.label for i in app.ns["runs"]]
        self.assertEqual(names, ["one", "two", "3"])
        app.run_pass()
        for url, _ in app.net.calls:
            self.assertNotIn("..", url)
            self.assertNotIn(" ", url)
        self.assertIn("ignored a bad DASH_REPOS entry", app.texts())

    def test_bad_username_never_reaches_the_url(self):
        app = App(ok_routes(), user="me&q=x y")
        app.frame(0)
        self.assertEqual(app.net.calls, [])
        self.assertIn("GITHUB_USERNAME looks wrong", app.texts())

    def test_long_repo_name_fits(self):
        app = App(ok_routes(), repos="o/" + "x" * 60)
        app.run_pass()
        labels = [s for s in app.drawn if s.startswith("xxx")]
        self.assertEqual(len(labels), 1)
        self.assertTrue(labels[0].endswith(".."))
        self.assertLessEqual(len(labels[0]) * 6, 320 - 36 - 6 * len("pass"))


class TokenSafety(unittest.TestCase):
    def check_never_drawn(self, app):
        for _ in range(3):
            app.run_pass()
        self.assertNotIn(TOKEN, app.texts())
        self.assertNotIn("SECRET0123", app.texts())
        for call in app.ns["items"]:
            self.assertNotIn(TOKEN, str(call.error))
        self.assertNotIn(TOKEN, str(app.ns["feed"].error))

    def test_happy_path(self):
        self.check_never_drawn(App(ok_routes()))

    def test_exception_text_with_token(self):
        err = OSError("connect failed, sent Authorization: Bearer " + TOKEN)
        app = App([("search/issues", err), ("actions/runs", err)])
        self.check_never_drawn(app)
        self.assertIn("***", app.ns["feed"].error)

    def test_error_body_echoing_token(self):
        body = {"message": "Bad credentials " + TOKEN}
        app = App([("search/issues", Response(403, body)), ("actions/runs", Response(403, body))])
        self.check_never_drawn(app)

    def test_every_draw_call_is_clean(self):
        app = App([("search/issues", ValueError(TOKEN)), ("actions/runs", Response(404, {"message": TOKEN}))])
        app.run_pass()
        for call in app.screen.text.call_args_list:
            self.assertNotIn(TOKEN, str(call))


if __name__ == "__main__":
    unittest.main()
