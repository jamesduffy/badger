import json

import requests

try:
    import secrets
except ImportError:
    secrets = None

try:
    import wifi
except ImportError:
    wifi = None

API = "https://api.github.com"
REFRESH_MS = 300_000
RETRY_MS = 30_000
MAX_REPOS = 3
TIMEOUT = 10

badge.mode(HIRES | VSYNC)
screen.font = font.sins
screen.antialias = image.X2

BG = color.rgb(13, 17, 23)
WHITE = color.rgb(235, 245, 255)
DIM = color.rgb(139, 148, 158)
GREEN = color.rgb(46, 160, 67)
RED = color.rgb(248, 81, 73)
AMBER = color.rgb(210, 153, 34)
LIME = color.rgb(211, 250, 55)


def setting(key):
    return str(getattr(secrets, key, "") or "").strip()


USER = setting("GITHUB_USERNAME")
TOKEN = setting("GITHUB_TOKEN")
REPOS = [r.strip() for r in setting("DASH_REPOS").split(",") if r.strip()][:MAX_REPOS]
DEMO = not (USER and TOKEN)

if DEMO:
    USER = USER or "octocat"
    REPOS = ["badger/home", "octo/api", "octo/web"]

HEADERS = {
    "User-Agent": "GitHub Universe Badge 2026",
    "Accept": "application/vnd.github+json",
    "Authorization": "Bearer " + TOKEN,
}

DEMO_VALUES = {"reviews": 3, "prs": 2, "issues": 5, "ci0": "success", "ci1": "failure", "ci2": "in_progress"}


def search_count(data):
    return data["total_count"]


def latest_run(data):
    runs = data["workflow_runs"]
    if not runs:
        return "none"
    return runs[0]["conclusion"] or runs[0]["status"]


class Item:
    def __init__(self, name, label, url, parse):
        self.name = name
        self.label = label
        self.url = url
        self.parse = parse
        self.value = DEMO_VALUES[name] if DEMO else None


def search_url(query):
    return API + "/search/issues?q=" + query + "&per_page=1"


counts = [
    Item("reviews", "Review requests", search_url("is:pr+is:open+review-requested:" + USER), search_count),
    Item("prs", "My open PRs", search_url("is:pr+is:open+author:" + USER), search_count),
    Item("issues", "Assigned issues", search_url("is:issue+is:open+assignee:" + USER), search_count),
]
runs = [
    Item(
        "ci%d" % i,
        repo.split("/")[-1],
        API + "/repos/" + repo + "/actions/runs?per_page=1&status=completed&exclude_pull_requests=true",
        latest_run,
    )
    for i, repo in enumerate(REPOS)
]
items = counts + runs


class Feed:
    """One blocking request per step (about a second each), spaced out by the frame loop."""

    def __init__(self):
        self.index = 0
        self.error = None
        self.next_at = 0
        self.updated = badge.ticks if DEMO else None

    def refresh(self):
        self.index = 0
        self.error = None
        self.next_at = 0

    def step(self):
        now = badge.ticks
        if DEMO or now < self.next_at:
            return

        if wifi is not None and not wifi.is_connected():
            if not wifi.connect():
                self.error = wifi.status()[1]
            return

        item = items[self.index]
        try:
            response = requests.get(item.url, headers=HEADERS, timeout=TIMEOUT)
            try:
                if response.status_code != 200:
                    message = response.json().get("message", "") if response.status_code in (401, 403) else ""
                    raise OSError("HTTP %d %s" % (response.status_code, message))
                item.value = item.parse(response.json())
            finally:
                response.close()
        except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError) as e:
            self.error = str(e)
            self.next_at = now + RETRY_MS
            return

        self.error = None
        self.index += 1
        if self.index >= len(items):
            self.index = 0
            self.updated = now
            self.next_at = now + REFRESH_MS


def run_colour(value):
    if value == "success":
        return GREEN, "pass"
    if value in ("failure", "timed_out", "startup_failure"):
        return RED, "fail"
    if value in ("in_progress", "queued", "waiting"):
        return AMBER, "running"
    if value is None:
        return DIM, "..."
    return DIM, value


def count_colour(item):
    if item.value is None:
        return DIM
    if item is counts[0] and item.value > 0:
        return AMBER
    return WHITE


def draw_row(label, value_text, value_pen, y):
    screen.pen = DIM
    screen.text(label, 12, y)
    screen.pen = value_pen
    width, _ = screen.measure_text(value_text)
    screen.text(value_text, screen.width - 12 - width, y)


def draw_status(feed):
    screen.pen = DIM
    if feed.error:
        screen.pen = RED
        text = feed.error[:44]
    elif DEMO:
        text = "demo data, set GITHUB_USERNAME and GITHUB_TOKEN"
    elif feed.updated is None:
        text = "loading..."
    else:
        text = "updated %dm ago, Select refreshes" % ((badge.ticks - feed.updated) // 60000)
    screen.text(text, 12, screen.height - 18)


feed = Feed()


def update():
    screen.pen = BG
    screen.clear()

    if badge.pressed(BUTTON_SELECT):
        feed.refresh()
    feed.step()

    screen.pen = LIME
    screen.text("@" + USER, 12, 10)

    y = 38
    for item in counts:
        text = "-" if item.value is None else str(item.value)
        draw_row(item.label, text, count_colour(item), y)
        y += 26

    if runs:
        screen.pen = DIM
        screen.text("CI, latest run", 12, y + 2)
        y += 26
        for item in runs:
            pen, text = run_colour(item.value)
            draw_row(item.label, text, pen, y)
            y += 24

    draw_status(feed)

    if badge.pressed(BUTTON_BACK):
        reset()


run(update)
