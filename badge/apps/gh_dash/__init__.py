import gc

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
API_VERSION = "2022-11-28"
REFRESH_MS = 300_000
RETRY_MS = 30_000
RATE_LIMIT_MS = 60_000
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


NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."


def valid(text, extra=""):
    return bool(text) and all(c in NAME_CHARS + extra for c in text)


def parse_repo(entry):
    """owner/repo or owner/repo@branch as (name, branch), None for anything else."""
    name, _, branch = entry.partition("@")
    owner, _, repo = name.partition("/")
    if valid(owner) and valid(repo) and (not branch or valid(branch, "/")):
        return name, branch
    return None


USER = setting("GITHUB_USERNAME")
TOKEN = setting("GITHUB_TOKEN")
entries = [r.strip() for r in setting("DASH_REPOS").split(",") if r.strip()]
REPOS = [parse_repo(r) for r in entries]
NOTE = ""
if None in REPOS:
    NOTE = "ignored a bad DASH_REPOS entry"
if USER and not valid(USER):
    NOTE = "GITHUB_USERNAME looks wrong"
    USER = ""
REPOS = [r for r in REPOS if r][:MAX_REPOS]
DEMO = not (USER and TOKEN)

if DEMO:
    USER = USER or "octocat"
    REPOS = [("badger/home", ""), ("octo/api", ""), ("octo/web", "")]

HEADERS = {
    "User-Agent": "GitHub Universe Badge 2026",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": API_VERSION,
    "Authorization": "Bearer " + TOKEN,
}

DEMO_VALUES = {"reviews": 3, "prs": 2, "issues": 5, "ci0": "success", "ci1": "failure", "ci2": "cancelled"}

# HTTP codes that only break one row, so the pass carries on to the next
ROW_ERRORS = {404: "not found, check name and token access", 301: "repo moved or renamed", 422: "search rejected"}


class ApiError(Exception):
    """args are (HTTP code, text for the screen)."""


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
        self.at = None  # ticks of the last good fetch
        self.error = None


def search_url(query):
    return API + "/search/issues?q=" + query + "&per_page=1"


def runs_url(repo, branch):
    url = API + "/repos/" + repo + "/actions/runs?per_page=1&status=completed&exclude_pull_requests=true"
    return url + "&branch=" + branch if branch else url


counts = [
    Item("reviews", "Review requests", search_url("is:pr+is:open+review-requested:" + USER), search_count),
    Item("prs", "My open PRs", search_url("is:pr+is:open+author:" + USER), search_count),
    Item("issues", "Assigned issues", search_url("is:issue+is:open+assignee:" + USER), search_count),
]
runs = [
    Item("ci%d" % i, repo.split("/")[-1], runs_url(repo, branch), latest_run)
    for i, (repo, branch) in enumerate(REPOS)
]
items = counts + runs


def scrub(text):
    """Error text goes on screen, so make sure the token can never be in it."""
    return text.replace(TOKEN, "***") if TOKEN else text


def get_json(url):
    gc.collect()  # the reply is parsed in one go, so start with a clean heap
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    try:
        code = response.status_code
        if code == 200:
            return response.json()
        try:
            message = str(response.json().get("message", ""))
        except (ValueError, AttributeError):
            message = ""
        if code == 401:
            raise ApiError(code, "token rejected (401)")
        if code == 403 and "rate limit" in message.lower():
            raise ApiError(code, "rate limited, waiting a minute")
        raise ApiError(code, ROW_ERRORS.get(code) or "HTTP %d %s" % (code, message))
    finally:
        response.close()


class Feed:
    """One blocking request per step (about a second each), spaced out by the frame loop."""

    def __init__(self):
        self.error = None  # problems with the whole pass: Wi-Fi, token, rate limit
        self.updated = badge.ticks if DEMO else None
        self.refresh()

    def refresh(self):
        self.index = 0
        self.error = None
        self.next_at = 0
        self.fetching = False

    def row_error(self):
        for item in items:
            if item.error:
                return item.label + ": " + item.error
        return None

    def step(self):
        now = badge.ticks
        if DEMO or now < self.next_at:
            return

        if wifi is not None and not wifi.is_connected():
            if not wifi.connect():
                code, saying = wifi.status()
                self.error = saying if code < 0 else None
            return

        # The request blocks the frame, so one frame goes up saying "updating" first.
        if not self.fetching:
            self.fetching = True
            return
        self.fetching = False

        item = items[self.index]
        wait = 0
        try:
            item.value = item.parse(get_json(item.url))
            item.at = now
            item.error = None
            self.error = None
            self.updated = now
        except ApiError as e:
            code, text = e.args
            item.error = scrub(text)
            if code not in ROW_ERRORS:
                self.error = item.error
                wait = REFRESH_MS if code == 401 else RATE_LIMIT_MS if code == 403 else RETRY_MS
        except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError, MemoryError) as e:
            item.error = scrub(str(e) or type(e).__name__)
            self.error = item.error
            wait = RETRY_MS

        self.index += 1
        if wait or self.index >= len(items):
            self.index = 0
            self.next_at = now + (wait or (RETRY_MS if self.row_error() else REFRESH_MS))


def run_colour(value):
    if value == "success":
        return GREEN, "pass"
    if value in ("failure", "timed_out", "startup_failure"):
        return RED, "fail"
    if value in ("in_progress", "queued", "waiting", "pending", "requested"):
        return AMBER, "running"
    if value == "action_required":
        return AMBER, "needs approval"
    if value in ("cancelled", "skipped", "neutral", "stale"):
        return DIM, value
    if value == "none":
        return DIM, "no runs"
    if value is None:
        return DIM, "..."
    return DIM, str(value)[:14]


def count_colour(item):
    if item.value is None:
        return DIM
    if item is counts[0] and item.value > 0:
        return AMBER
    return WHITE


def fit(text, width):
    """Cut text with ".." so it is no wider than width."""
    if screen.measure_text(text)[0] <= width:
        return text
    while text and screen.measure_text(text + "..")[0] > width:
        text = text[:-1]
    return text + ".."


def draw_row(item, value_text, value_pen, y):
    # a failed refresh leaves the old value up, dimmed with how old it is
    if item.error and item.value is not None and item.at is not None:
        value_text += " %dm old" % ((badge.ticks - item.at) // 60000)
        value_pen = DIM
    elif item.error:
        value_text, value_pen = "error", RED
    width, _ = screen.measure_text(value_text)
    screen.pen = DIM
    screen.text(fit(item.label, screen.width - 36 - width), 12, y)
    screen.pen = value_pen
    screen.text(value_text, screen.width - 12 - width, y)


def draw_status(feed):
    problem = feed.error or feed.row_error()
    screen.pen = DIM
    if problem:
        screen.pen = RED
        text = problem
    elif NOTE:
        text = NOTE
    elif DEMO:
        text = "demo data, set GITHUB_USERNAME and GITHUB_TOKEN"
    elif feed.fetching:
        text = "updating..."
    elif feed.updated is None:
        text = "loading..."
    else:
        text = "updated %dm ago, Select refreshes" % ((badge.ticks - feed.updated) // 60000)
    screen.text(fit(text, screen.width - 24), 12, screen.height - 18)


feed = Feed()


def update():
    # input and the blocking fetch come before any drawing, so the last frame
    # (with its "updating" mark) stays up while the request runs
    if badge.pressed(BUTTON_SELECT):
        feed.refresh()
    feed.step()

    screen.pen = BG
    screen.clear()

    screen.pen = LIME
    screen.text("@" + USER, 12, 10)

    if feed.fetching:
        screen.pen = LIME
        screen.shape(shape.arc(screen.width - 22, 20, 5, 8, 0, 270))

    y = 38
    for item in counts:
        text = "-" if item.value is None else str(item.value)
        draw_row(item, text, count_colour(item), y)
        y += 26

    if runs:
        screen.pen = DIM
        screen.text("CI, latest run", 12, y + 2)
        y += 26
        for item in runs:
            pen, text = run_colour(item.value)
            draw_row(item, text, pen, y)
            y += 24

    draw_status(feed)

    if badge.pressed(BUTTON_BACK):
        reset()


run(update)
