"""MeshCore feed: companion client. UNTESTED ON HARDWARE.

Frame layouts follow MyMesh.cpp and meshcore_py (sources in
editor/meshcore-feasibility.md). Demo mode needs no radio and runs in the
browser simulator. Live mode needs BLE pairing with a PIN, which the badge
BTstack build most likely does not have, so it stops with a clear message.
"""

import os
import sys
import time

APP_DIR = "/system/apps/meshcore_feed"
try:
    os.chdir(APP_DIR)
    sys.path.insert(0, APP_DIR)
except OSError:
    pass  # simulator may not have this path

badge.mode(HIRES | VSYNC)

# MeshCore companion BLE service, from docs.meshcore.io/companion_protocol
NUS = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # we write here
NUS_TX = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # node notifies here
BLE_PIN = 123456  # MeshCore FAQ 6.5 default. Change it on the node if you did.

# command codes (MyMesh.cpp)
CMD_APP_START, CMD_SEND_TXT, CMD_GET_CONTACTS = 1, 2, 4
CMD_GET_TIME, CMD_SYNC_NEXT, CMD_DEVICE_QUERY = 5, 10, 22
# response / push codes
R_CONTACTS_START, R_CONTACT, R_CONTACTS_END, R_SELF = 2, 3, 4, 5
R_SENT, R_DM, R_CH, R_TIME, R_NO_MORE, R_ERR = 6, 7, 8, 9, 10, 1
R_DM_V3, R_CH_V3 = 16, 17
P_MSG_WAITING = 0x83

CANNED = ["OK", "Got it, thanks", "Will reply later", "73"]
MAX_KEEP = 30  # messages kept
MAX_CONTACTS = 100  # contacts kept, newest heard first
CONFIRM_DELAY = 700  # ms before the confirm screen accepts SELECT
TXT_SIGNED = 2  # txt_type with a 4 byte signature before the text

BG = color.rgb(13, 17, 23)
FG = color.rgb(230, 237, 243)
DIM = color.rgb(125, 133, 144)
ACC = color.rgb(88, 58, 247)
GRN = color.rgb(63, 185, 80)
RED = color.rgb(248, 81, 73)

screen.font = font.sins


def u32(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)


def text_of(b):
    """bytes to printable ASCII. Never raises. Each multi-byte UTF-8 char
    becomes one "?" (the badge font is not known to cover it)."""
    out = ""
    for c in bytes(b):
        if c in (9, 10, 13):
            out += " "
        elif 32 <= c < 127:
            out += chr(c)
        elif c >= 0xC0:
            out += "?"
    return out.strip()


def name_of(b):
    return text_of(bytes(b).split(b"\x00")[0])


# frame builders (app to node). Layouts: MyMesh.cpp, meshcore_py send_msg
def frame_device_query():
    return bytes([CMD_DEVICE_QUERY, 3])  # app_target_ver 3 selects the V3 message codes


def frame_app_start(name=b"badge"):
    return bytes([CMD_APP_START, 3]) + bytes(6) + name  # name starts at offset 8


def frame_send_txt(prefix, text, ts):
    # [2, txt_type 0, attempt 0, u32 LE timestamp, 6 byte pubkey prefix, text]
    return bytes([CMD_SEND_TXT, 0, 0]) + ts.to_bytes(4, "little") + bytes(prefix) + text


# frame parsers (node to app). Return None when the frame is too short.
def parse_contact(f):
    # 148 bytes with MAX_PATH_SIZE 64: key 1-32, type, flags, path_len, path,
    # name 32, last_advert u32, lat, lon, lastmod. The 48 byte tail does not
    # depend on the path size, so offsets are counted from the end.
    if len(f) < 85:
        return None
    return {"prefix": bytes(f[1:7]), "name": name_of(f[-48:-16]), "last": u32(f, len(f) - 16)}


def parse_dm(f, v3):
    # v3: snr, 2 reserved, prefix 4-9, path_len, txt_type 11, ts 12-15, text 16
    # v2: prefix 1-6, path_len, txt_type 8, ts 9-12, text 13
    # signed messages (txt_type 2) carry 4 extra bytes before the text
    base = 16 if v3 else 13
    if len(f) <= base:
        return None
    p = 4 if v3 else 1
    txt_type = f[11] if v3 else f[8]
    start = base + (4 if txt_type == TXT_SIGNED else 0)
    return bytes(f[p:p + 6]), text_of(f[start:]), u32(f, base - 4)


def parse_channel(f, v3):
    # v3: snr, 2 reserved, channel_idx 4, path_len, txt_type, ts 7-10, text 11
    # v2: channel_idx 1, path_len, txt_type, ts 4-7, text 8
    base = 11 if v3 else 8
    if len(f) <= base:
        return None
    return text_of(f[base:]), u32(f, base - 4)


def adv_info(adv, uuid_le):
    """(has the service uuid, local name) from raw advertising data"""
    name, i = "", 0
    while i + 1 < len(adv) and adv[i]:
        if adv[i + 1] in (8, 9):
            name = text_of(adv[i + 2:i + 1 + adv[i]])
        i += adv[i] + 1
    return uuid_le in adv, name


def ago(seconds):
    if seconds is None or seconds < 0:
        return "?"
    if seconds < 90:
        return "%ds" % seconds
    if seconds < 5400:
        return "%dm" % (seconds // 60)
    if seconds < 172800:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


# ---------------------------------------------------------------- state

app = {
    "screen": "start",  # start, feed, contacts, reply
    "demo": True,
    "status": "",
    "node": "",
    "messages": [],  # dicts: kind, who, prefix, text, ts
    "contacts": [],  # dicts: prefix, name, last
    "node_time": None,
    "sel": 0,
    "pick": 0,
    "target": None,  # (prefix, who) chosen for a reply, fixed so new messages cannot change it
    "text": "",  # canned reply chosen
    "confirm_at": 0,
    "toast": "",
    "toast_until": 0,
}


def toast(msg):
    app["toast"] = msg
    app["toast_until"] = badge.ticks + 2500


def add_message(kind, prefix, text, ts):
    who = "ch" if kind == "ch" else prefix.hex() if hasattr(prefix, "hex") else "?"
    text = text or "(empty)"
    for c in app["contacts"]:
        if kind == "dm" and c["prefix"] == prefix:
            who = c["name"]
    app["messages"].insert(0, {"kind": kind, "who": who, "prefix": prefix, "text": text, "ts": ts})
    del app["messages"][MAX_KEEP:]


# ---------------------------------------------------------------- demo data

DEMO_MESSAGES = [
    ("ch", b"", "Pete: anyone on 910 this morning?", 0),
    ("dm", b"\x01\x02\x03\x04\x05\x06", "Ready for lunch? Hall B", 0),
    ("ch", b"", "Rae: repeater on the hill is back up", 0),
    ("dm", b"\x0a\x0b\x0c\x0d\x0e\x0f", "Badge looks good, nice work", 0),
]
DEMO_CONTACTS = [
    (b"\x01\x02\x03\x04\x05\x06", "Sam", 120),
    (b"\x0a\x0b\x0c\x0d\x0e\x0f", "Priya", 3600),
    (b"\x11\x12\x13\x14\x15\x16", "Hill rptr", 86400),
]


def start_demo():
    app["demo"] = True
    app["node"] = "demo-node"
    app["node_time"] = 1000000
    app["contacts"] = [{"prefix": p, "name": n, "last": app["node_time"] - a} for p, n, a in DEMO_CONTACTS]
    app["messages"] = []
    for kind, prefix, text, ts in reversed(DEMO_MESSAGES):
        add_message(kind, prefix, text, ts)
    app["status"] = "demo data, nothing is sent"
    app["screen"] = "feed"


# ---------------------------------------------------------------- live BLE client


class Live:
    """BLE central for a MeshCore companion node. Untested on hardware."""

    def __init__(self):
        import bluetooth

        self.bt = bluetooth
        self.ble = bluetooth.BLE()
        self.events = []
        self.state = "scan"
        self.deadline = badge.ticks + 9000
        self.conn = None
        self.rx = self.tx = self.cccd = None
        self.svc = None
        self.mtu = 23
        self.queue = []
        self.busy = False
        self.found = None
        self.contact_buf = []
        self.bad_frames = 0
        self.ble.active(True)
        # MeshCore needs an encrypted, MITM protected link with a static PIN.
        # MicroPython only builds pairing for NimBLE. BTstack (CYW43 boards)
        # has no gap_pair/passkey in the usual build, so check before scanning.
        if not hasattr(self.ble, "gap_pair"):
            raise OSError("this firmware cannot pair (no gap_pair)")
        try:
            self.ble.config(bond=True, mitm=True, le_secure=True, io=3)
            self.ble.config(mtu=185)
        except Exception as error:
            raise OSError("this firmware cannot pair (%s)" % error)
        self.ble.irq(self.irq)
        self.uuid = bluetooth.UUID(NUS)
        self.uuid_le = bytes(reversed(bytes.fromhex(NUS.replace("-", ""))))  # advertising order
        self.ble.gap_scan(8000, 100000, 30000, True)
        app["status"] = "scanning for MeshCore node"

    def go(self, state, seconds, status=None):
        self.state = state
        self.deadline = badge.ticks + seconds * 1000
        if status:
            app["status"] = status

    def irq(self, event, data):
        # keep this small, copy anything we keep (buffers die after return)
        if event == 5:
            if self.found is None:
                addr_type, addr, _, _, adv = data
                has_uuid, name = adv_info(bytes(adv), self.uuid_le)
                # the uuid can be in the scan response only, so also accept the default name
                if has_uuid or name.startswith("MeshCore"):
                    self.found = (addr_type, bytes(addr), name)
            return
        if len(self.events) > 80:
            return
        if event == 18:  # notify
            self.events.append((18, bytes(data[2])))
        elif event == 7:
            self.events.append((7, data[0]))
        elif event == 9:
            self.events.append((9, (data[1], data[2])))
        elif event == 11:
            self.events.append((11, (data[2], self.bt.UUID(data[4]))))
        elif event == 13:  # descriptor result: conn, handle, uuid
            self.events.append((13, (data[1], self.bt.UUID(data[2]))))
        elif event in (6, 8, 10, 12, 14, 17, 21, 28):
            self.events.append((event, tuple(data)))
        elif event == 31:  # passkey action
            conn, action, _ = data
            if action == 2:  # we type the PIN
                try:
                    self.ble.gap_passkey(conn, action, BLE_PIN)
                except Exception:
                    self.events.append((31, None))  # BTstack: not supported

    def send(self, frame):
        self.queue.append(bytes(frame))
        self.pump()

    def pump(self):
        if self.busy or not self.queue or self.rx is None:
            return
        frame = self.queue.pop(0)
        self.busy = True
        self.ble.gattc_write(self.conn, self.rx, frame, 1)

    def step(self):
        if self.state == "scan" and self.found is not None:
            self.ble.gap_scan(None)
            app["node"] = self.found[2] or "node"
            self.go("connect", 10, "connecting to " + app["node"])
            self.ble.gap_connect(self.found[0], self.found[1], 8000)
        if badge.ticks > self.deadline and self.state != "ready":
            raise OSError("no node found" if self.state == "scan" else "timeout (%s)" % self.state)
        while self.events:
            ev, d = self.events.pop(0)
            if ev == 7:
                self.conn = d
                self.go("pair", 30, "pairing (PIN %d)" % BLE_PIN)
                self.ble.gap_pair(self.conn)
            elif ev == 31:
                raise OSError("this firmware cannot enter a PIN")
            elif ev == 28:  # encryption update: conn, encrypted, authed, bonded, key size
                if d[1] and self.state == "pair":
                    self.go("disc", 15, "paired, discovering")
                    self.ble.gattc_exchange_mtu(self.conn)
                    self.ble.gattc_discover_services(self.conn, self.uuid)
                elif not d[1]:
                    raise OSError("pairing failed (wrong PIN?)")
            elif ev == 21:
                self.mtu = d[1]
            elif ev == 9:
                self.svc = (d[0], d[1])
            elif ev == 10:
                if self.svc is None:
                    raise OSError("service not found")
                self.ble.gattc_discover_characteristics(self.conn, *self.svc)
            elif ev == 11:
                if d[1] == self.bt.UUID(NUS_RX):
                    self.rx = d[0]
                elif d[1] == self.bt.UUID(NUS_TX):
                    self.tx = d[0]
            elif ev == 12:
                if self.rx is None or self.tx is None:
                    raise OSError("characteristics missing")
                # look for the real CCCD (0x2902) after the TX value handle
                self.ble.gattc_discover_descriptors(self.conn, self.tx, self.svc[1])
            elif ev == 13:
                if d[1] == self.bt.UUID(0x2902) and self.cccd is None and d[0] > self.tx:
                    self.cccd = d[0]
            elif ev == 14:
                if self.cccd is None:
                    raise OSError("notify descriptor not found")
                self.go("subscribe", 8)
                self.busy = True
                self.ble.gattc_write(self.conn, self.cccd, b"\x01\x00", 1)
            elif ev == 17:
                self.busy = False
                if d[2] != 0:
                    raise OSError("node refused write (status %d), not paired?" % d[2])
                if self.state == "subscribe":
                    self.go("ready", 0, "connected")
                    self.send(frame_device_query())
                    self.send(frame_app_start())
                    self.send(bytes([CMD_GET_TIME]))
                    self.send(bytes([CMD_GET_CONTACTS]))
                    self.send(bytes([CMD_SYNC_NEXT]))
                self.pump()
            elif ev == 18:
                try:
                    self.frame(d)
                except Exception:  # a malformed frame must not drop the link
                    self.bad_frames += 1
            elif ev == 8:
                raise OSError("disconnected")
            elif ev == 6 and self.state == "scan" and self.found is None:
                raise OSError("no node found")

    def can_send(self):
        return self.state == "ready" and app["node_time"] is not None

    def send_reply(self, prefix, text):
        if not self.can_send():
            toast("not ready, not sent")
            return
        data = text.encode()
        frame = frame_send_txt(prefix, data, app["node_time"])
        if len(frame) > self.mtu - 3:
            toast("MTU too small, not sent")
            return
        self.send(frame)

    def frame(self, f):
        if not f:
            return
        code = f[0]
        if code == R_SELF and len(f) > 58:
            app["node"] = name_of(f[58:]) or app["node"]
        elif code == R_TIME and len(f) >= 5:
            app["node_time"] = u32(f, 1)
        elif code == R_CONTACTS_START:
            self.contact_buf = []
        elif code == R_CONTACT:
            c = parse_contact(f)
            if c and len(self.contact_buf) < 300:
                self.contact_buf.append(c)
        elif code == R_CONTACTS_END:
            self.contact_buf.sort(key=lambda c: -c["last"])
            app["contacts"] = self.contact_buf[:MAX_CONTACTS]
            self.contact_buf = []
        elif code in (R_DM, R_DM_V3):
            m = parse_dm(f, code == R_DM_V3)
            if m:
                add_message("dm", m[0], m[1], m[2])
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code in (R_CH, R_CH_V3):
            m = parse_channel(f, code == R_CH_V3)
            if m:
                add_message("ch", b"", m[0], m[1])
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code == P_MSG_WAITING:
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code == R_SENT:
            toast("sent")
        elif code == R_ERR:
            toast("node returned error")

    def close(self):
        try:
            if self.conn is not None:
                self.ble.gap_disconnect(self.conn)
        except Exception:
            pass
        try:
            self.ble.gap_scan(None)
        except Exception:
            pass
        try:
            self.ble.irq(None)
            self.ble.active(False)
        except Exception:
            pass


live = None


def start_live():
    global live
    app["demo"] = False
    app["messages"] = []
    app["contacts"] = []
    app["screen"] = "feed"
    try:
        live = Live()
    except Exception as error:  # no bluetooth module, stub, BLE busy
        live = None
        try:
            import bluetooth

            bluetooth.BLE().active(False)
        except Exception:
            pass
        app["status"] = "Live stopped: %s" % error
        app["demo"] = True
        app["screen"] = "start"


def on_exit():
    if live:
        live.close()


# ---------------------------------------------------------------- drawing


def line_height():
    return screen.measure_text("Ag")[1] + 3


def fit(text, width):
    """cut text with ".." so it is at most width pixels wide"""
    if screen.measure_text(text)[0] <= width:
        return text
    while text and screen.measure_text(text + "..")[0] > width:
        text = text[:-1]
    return text + ".."


def wrap(text, width):
    lines, cur = [], ""
    for word in text.split(" "):
        while screen.measure_text(word)[0] > width and len(word) > 1:
            # one word wider than the screen (URL, long id): split it
            n = len(word) - 1
            while n > 1 and screen.measure_text(word[:n])[0] > width:
                n -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:n])
            word = word[n:]
        trial = word if not cur else cur + " " + word
        if screen.measure_text(trial)[0] <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def header(title):
    screen.pen = ACC
    screen.rectangle(0, 0, screen.width, 22)
    screen.pen = FG
    screen.text(fit(title, screen.width - 70), 6, 4)
    label = "DEMO" if app["demo"] else "LIVE"
    screen.pen = GRN if not app["demo"] else DIM
    screen.text(label, screen.width - screen.measure_text(label)[0] - 6, 4)


def footer(hint):
    screen.pen = DIM
    screen.text(fit(hint, screen.width - 12), 6, screen.height - line_height())
    if badge.ticks < app["toast_until"]:
        screen.pen = GRN
        screen.text(fit(app["toast"], screen.width - 12), 6, screen.height - 2 * line_height())
    elif not app["demo"] and app["status"] and app["screen"] != "start":
        screen.text(fit(app["status"], screen.width - 12), 6, screen.height - 2 * line_height())


def draw_start():
    header("MeshCore feed")
    lh = line_height()
    y = 34
    screen.pen = FG
    items = ["Demo (canned messages)", "Live (BLE, probably fails)"]
    for i, label in enumerate(items):
        screen.pen = ACC if i == app["pick"] else DIM
        screen.text(("> " if i == app["pick"] else "  ") + label, 10, y + i * (lh + 6))
    screen.pen = RED
    y += 2 * (lh + 6) + 8
    note = "Live needs BLE pairing with a PIN. This badge's BLE stack most likely can't pair, so expect an error. Not run on hardware yet. Demo always works."
    for line in wrap(note, screen.width - 20):
        screen.text(line, 10, y)
        y += lh
    if app["status"]:
        screen.pen = DIM
        for line in wrap(app["status"], screen.width - 20):
            screen.text(line, 10, y + 6)
            y += lh
    footer("UP/DOWN pick   SELECT go")


def draw_feed():
    header("Messages (%d)" % len(app["messages"]))
    lh = line_height()
    y = 28
    msgs = app["messages"]
    if not msgs:
        screen.pen = DIM
        screen.text(app["status"] or "waiting for messages", 8, y)
    top = max(0, app["sel"] - 2)
    for i in range(top, len(msgs)):
        m = msgs[i]
        lines = wrap(m["text"], screen.width - 28)[:3]
        h = (len(lines) + 1) * lh + 4
        if y + h > screen.height - 2 * lh:
            break
        if i == app["sel"]:
            screen.pen = color.rgb(33, 38, 45)
            screen.rectangle(0, y - 2, screen.width, h)
        screen.pen = ACC if m["kind"] == "ch" else GRN
        screen.text(fit(("# " if m["kind"] == "ch" else "DM ") + m["who"], screen.width - 12), 6, y)
        screen.pen = FG
        for j, line in enumerate(lines):
            screen.text(line, 14, y + (j + 1) * lh)
        y += h
    footer("SELECT reply (DM)  RIGHT contacts  BACK menu")


def draw_contacts():
    header("Contacts (%d)" % len(app["contacts"]))
    lh = line_height()
    y = 28
    now = app["node_time"]
    for i in range(max(0, app["sel"] - 4), len(app["contacts"])):
        c = app["contacts"][i]
        if y + lh > screen.height - 2 * lh:
            break
        if i == app["sel"]:
            screen.pen = color.rgb(33, 38, 45)
            screen.rectangle(0, y - 1, screen.width, lh)
        screen.pen = FG
        age = ago(now - c["last"]) if now else "?"
        screen.text(fit(c["name"] or "(no name)", screen.width - 70), 8, y)
        screen.pen = DIM
        screen.text(age, screen.width - screen.measure_text(age)[0] - 8, y)
        y += lh
    footer("LEFT messages  BACK menu")


def draw_reply():
    header("Reply to " + app["target"][1])
    lh = line_height()
    for i, text in enumerate(CANNED):
        screen.pen = ACC if i == app["pick"] else FG
        screen.text(("> " if i == app["pick"] else "  ") + text, 10, 32 + i * (lh + 4))
    footer("SELECT choose   BACK cancel")


def draw_confirm():
    header("Confirm send")
    lh = line_height()
    y = 32
    screen.pen = RED
    screen.text("demo: nothing is sent" if app["demo"] else "This sends a real MeshCore message", 10, y)
    y += lh + 8
    screen.pen = DIM
    screen.text("To:", 10, y)
    screen.pen = FG
    screen.text(fit(app["target"][1], screen.width - 60), 50, y)
    y += lh + 4
    screen.pen = DIM
    screen.text("Text:", 10, y)
    screen.pen = FG
    for line in wrap('"' + app["text"] + '"', screen.width - 60):
        screen.text(line, 50, y)
        y += lh
    footer("SELECT send now   BACK cancel")


def draw():
    screen.pen = BG
    screen.rectangle(screen.clip)
    {"start": draw_start, "feed": draw_feed, "contacts": draw_contacts, "reply": draw_reply, "confirm": draw_confirm}[app["screen"]]()


# ---------------------------------------------------------------- input


def handle_input():
    s = app["screen"]
    size = len(app["messages"]) if s == "feed" else len(app["contacts"])
    app["sel"] = max(0, min(app["sel"], size - 1)) if s in ("feed", "contacts") else app["sel"]
    down = badge.pressed(BUTTON_DOWN)
    up = badge.pressed(BUTTON_UP)
    if s == "start":
        if up or down:
            app["pick"] = 1 - app["pick"]
        if badge.pressed(BUTTON_SELECT):
            if app["pick"] == 0:
                start_demo()
            else:
                start_live()
    elif s in ("feed", "contacts"):
        if down and app["sel"] < size - 1:
            app["sel"] += 1
        if up and app["sel"] > 0:
            app["sel"] -= 1
        if badge.pressed(BUTTON_RIGHT) and s == "feed":
            app["screen"], app["sel"] = "contacts", 0
        elif badge.pressed(BUTTON_LEFT) and s == "contacts":
            app["screen"], app["sel"] = "feed", 0
        if badge.pressed(BUTTON_BACK):
            on_exit()
            globals()["live"] = None
            app["demo"] = True
            app["status"] = ""
            app["screen"], app["sel"] = "start", 0
        if s == "feed" and badge.pressed(BUTTON_SELECT) and app["messages"]:
            m = app["messages"][app["sel"]]
            if m["kind"] == "dm" and m["prefix"]:
                # remember who we reply to now, new messages shift the list
                app["target"] = (m["prefix"], m["who"])
                app["screen"], app["pick"] = "reply", 0
            else:
                toast("reply only for DMs")
    elif s == "reply":
        if down and app["pick"] < len(CANNED) - 1:
            app["pick"] += 1
        if up and app["pick"] > 0:
            app["pick"] -= 1
        if badge.pressed(BUTTON_BACK):
            app["screen"] = "feed"
        elif badge.pressed(BUTTON_SELECT):
            # SELECT only picks the text. Sending needs a second SELECT on the confirm screen.
            app["text"] = CANNED[app["pick"]]
            app["confirm_at"] = badge.ticks
            app["screen"] = "confirm"
    elif s == "confirm":
        if badge.pressed(BUTTON_BACK):
            app["screen"] = "reply"
        elif badge.pressed(BUTTON_SELECT) and badge.ticks - app["confirm_at"] > CONFIRM_DELAY:
            if live is not None and not app["demo"]:
                live.send_reply(app["target"][0], app["text"])
            else:
                toast("demo: not sent")
            app["screen"] = "feed"


def update():
    handle_input()
    if live is not None:
        try:
            live.step()
        except Exception as error:
            app["status"] = "Live stopped: %s" % error
            on_exit()
            globals()["live"] = None
            app["demo"] = True
            app["screen"] = "start"
    draw()


run(update)
