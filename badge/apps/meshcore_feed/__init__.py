"""MeshCore feed: read-only-ish companion client. UNTESTED ON HARDWARE.

Frame handling follows the MeshCore companion protocol notes in
editor/meshcore-feasibility.md (sources linked there). Demo mode needs no
radio and runs in the browser simulator.
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

CANNED = ["OK", "On my way", "Got it, thanks", "Call you later", "73"]
MAX_KEEP = 30

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
    return bytes(b).decode("utf-8", "replace")


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
    "toast": "",
    "toast_until": 0,
}


def toast(msg):
    app["toast"] = msg
    app["toast_until"] = badge.ticks + 2500


def add_message(kind, prefix, text, ts):
    who = "ch" if kind == "ch" else prefix.hex() if hasattr(prefix, "hex") else "?"
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
        self.conn = None
        self.rx = self.tx = None
        self.svc = None
        self.mtu = 23
        self.queue = []
        self.busy = False
        self.found = None
        self.contact_buf = []
        self.draining = False
        self.started = badge.ticks
        self.ble.active(True)
        self.ble.config(mtu=185)
        # pairing: MeshCore requires an encrypted link with a static PIN
        # (see report). Firmware support for this on RP2 is unknown.
        self.ble.config(bond=True, mitm=True, le_secure=True, io=3)
        self.ble.irq(self.irq)
        self.uuid = bluetooth.UUID(NUS)
        self.ble.gap_scan(8000, 100000, 30000, True)
        app["status"] = "scanning for MeshCore node"

    def irq(self, event, data):
        # keep this small, copy anything we keep (buffers die after return)
        if event == 5:
            if self.found is None:
                addr_type, addr, _, _, adv = data
                adv = bytes(adv)
                if bytes(self.uuid) in adv:
                    name = ""
                    i = 0
                    while i + 1 < len(adv) and adv[i]:
                        if adv[i + 1] in (8, 9):
                            name = adv[i + 2:i + 1 + adv[i]].decode("utf-8", "replace")
                        i += adv[i] + 1
                    self.found = (addr_type, bytes(addr), name)
            return
        if len(self.events) > 40:
            return
        if event == 18:  # notify
            self.events.append((18, bytes(data[2])))
        elif event == 7:
            self.events.append((7, data[0]))
        elif event == 9:
            self.events.append((9, (data[1], data[2], bytes(data[3]))))
        elif event == 11:
            self.events.append((11, (data[2], bytes(data[4]))))
        elif event in (6, 8, 10, 12, 17, 21, 28):
            self.events.append((event, tuple(data)))
        elif event == 31:  # passkey action
            conn, action, _ = data
            if action == 2:  # we type the PIN
                self.ble.gap_passkey(conn, action, BLE_PIN)

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
        if self.state == "scan" and self.found is None and badge.ticks - self.started > 9000:
            raise OSError("no node found")
        if self.state == "scan" and self.found is not None:
            self.ble.gap_scan(None)
            self.state = "connect"
            app["node"] = self.found[2] or "node"
            app["status"] = "connecting to " + app["node"]
            self.ble.gap_connect(self.found[0], self.found[1], 8000)
        while self.events:
            ev, d = self.events.pop(0)
            if ev == 7:
                self.conn = d
                self.state = "pair"
                app["status"] = "pairing (PIN %d)" % BLE_PIN
                self.ble.gap_pair(self.conn)
            elif ev == 28:  # encryption update: conn, encrypted, authed, bonded, key size
                if d[1]:
                    app["status"] = "encrypted, discovering"
                    self.state = "disc"
                    self.ble.gattc_exchange_mtu(self.conn)
                    self.ble.gattc_discover_services(self.conn, self.uuid)
                else:
                    raise OSError("pairing failed")
            elif ev == 21:
                self.mtu = d[1]
            elif ev == 9:
                self.svc = (d[0], d[1])
            elif ev == 10:
                if self.svc is None:
                    raise OSError("service not found")
                self.ble.gattc_discover_characteristics(self.conn, *self.svc)
            elif ev == 11:
                if d[1] == bytes(self.bt.UUID(NUS_RX)):
                    self.rx = d[0]
                elif d[1] == bytes(self.bt.UUID(NUS_TX)):
                    self.tx = d[0]
            elif ev == 12:
                if self.rx is None or self.tx is None:
                    raise OSError("characteristics missing")
                self.state = "subscribe"
                # assumes the CCCD sits right after the value handle
                self.ble.gattc_write(self.conn, self.tx + 1, b"\x01\x00", 1)
                self.busy = True
            elif ev == 17:
                self.busy = False
                if self.state == "subscribe":
                    self.state = "ready"
                    app["status"] = "connected"
                    self.send(bytes([CMD_DEVICE_QUERY, 3]))
                    self.send(bytes([CMD_APP_START, 3]) + bytes(6) + b"badge")
                    self.send(bytes([CMD_GET_TIME]))
                    self.send(bytes([CMD_GET_CONTACTS]))
                    self.send(bytes([CMD_SYNC_NEXT]))
                self.pump()
            elif ev == 18:
                self.frame(d)
            elif ev == 8:
                raise OSError("disconnected")

    def send_reply(self, prefix, text):
        data = text.encode()
        if len(data) + 16 > self.mtu - 3:
            toast("MTU too small")
            return
        ts = app["node_time"] or 0
        frame = bytes([CMD_SEND_TXT, 0, 0]) + ts.to_bytes(4, "little") + prefix + data
        self.send(frame)

    def frame(self, f):
        if not f:
            return
        code = f[0]
        if code == R_SELF and len(f) > 58:
            app["node"] = text_of(f[58:])
        elif code == R_TIME and len(f) >= 5:
            app["node_time"] = u32(f, 1)  # layout inferred, see report
        elif code == R_CONTACTS_START:
            self.contact_buf = []
        elif code == R_CONTACT and len(f) >= 85:
            # fixed tail: name(32) + last_advert(4) + lat(4) + lon(4) + lastmod(4)
            name = text_of(f[-48:-16]).split("\x00")[0]
            self.contact_buf.append({"prefix": bytes(f[1:7]), "name": name, "last": u32(f, len(f) - 16)})
        elif code == R_CONTACTS_END:
            self.contact_buf.sort(key=lambda c: -c["last"])
            app["contacts"] = self.contact_buf
        elif code == R_DM and len(f) > 12:
            add_message("dm", bytes(f[1:7]), text_of(f[13:]), u32(f, 9))
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code == R_DM_V3 and len(f) > 16:
            add_message("dm", bytes(f[4:10]), text_of(f[16:]), u32(f, 12))
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code == R_CH and len(f) > 8:
            add_message("ch", b"", text_of(f[8:]), u32(f, 4))
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code == R_CH_V3 and len(f) > 11:
            add_message("ch", b"", text_of(f[11:]), u32(f, 7))
            self.send(bytes([CMD_SYNC_NEXT]))
        elif code == R_NO_MORE:
            pass
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
        app["status"] = "BLE unavailable: %s" % error
        app["demo"] = True
        app["screen"] = "start"


def on_exit():
    if live:
        live.close()


# ---------------------------------------------------------------- drawing


def line_height():
    return screen.measure_text("Ag")[1] + 3


def wrap(text, width):
    lines, cur = [], ""
    for word in text.split(" "):
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
    screen.text(title, 6, 4)
    label = "DEMO" if app["demo"] else "LIVE"
    screen.pen = GRN if not app["demo"] else DIM
    screen.text(label, screen.width - screen.measure_text(label)[0] - 6, 4)


def footer(hint):
    screen.pen = DIM
    screen.text(hint, 6, screen.height - line_height())
    if badge.ticks < app["toast_until"]:
        screen.pen = GRN
        screen.text(app["toast"], 6, screen.height - 2 * line_height())


def draw_start():
    header("MeshCore feed")
    lh = line_height()
    y = 34
    screen.pen = FG
    items = ["Demo (canned messages)", "Live (BLE to my node)"]
    for i, label in enumerate(items):
        screen.pen = ACC if i == app["pick"] else DIM
        screen.text(("> " if i == app["pick"] else "  ") + label, 10, y + i * (lh + 6))
    screen.pen = RED
    y += 2 * (lh + 6) + 8
    for line in wrap("Live mode is untested on hardware. Needs a BLE companion node (PIN %d)." % BLE_PIN, screen.width - 20):
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
        lines = wrap(m["text"], screen.width - 20)[:3]
        h = (len(lines) + 1) * lh + 4
        if y + h > screen.height - 2 * lh:
            break
        if i == app["sel"]:
            screen.pen = color.rgb(33, 38, 45)
            screen.rectangle(0, y - 2, screen.width, h)
        screen.pen = ACC if m["kind"] == "ch" else GRN
        screen.text(("# " if m["kind"] == "ch" else "DM ") + m["who"], 6, y)
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
        screen.text(c["name"][:22], 8, y)
        age = ago(now - c["last"]) if now else "?"
        screen.pen = DIM
        screen.text(age, screen.width - screen.measure_text(age)[0] - 8, y)
        y += lh
    footer("LEFT messages  BACK menu")


def draw_reply():
    m = app["messages"][app["sel"]]
    header("Reply to " + m["who"])
    lh = line_height()
    for i, text in enumerate(CANNED):
        screen.pen = ACC if i == app["pick"] else FG
        screen.text(("> " if i == app["pick"] else "  ") + text, 10, 32 + i * (lh + 4))
    footer("SELECT send   BACK cancel")


def draw():
    screen.pen = BG
    screen.rectangle(screen.clip)
    {"start": draw_start, "feed": draw_feed, "contacts": draw_contacts, "reply": draw_reply}[app["screen"]]()


# ---------------------------------------------------------------- input


def handle_input():
    s = app["screen"]
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
        size = len(app["messages"] if s == "feed" else app["contacts"])
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
            app["screen"], app["sel"] = "start", 0
        if s == "feed" and badge.pressed(BUTTON_SELECT) and app["messages"]:
            m = app["messages"][app["sel"]]
            if m["kind"] == "dm" and m["prefix"]:
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
        if badge.pressed(BUTTON_SELECT):
            m = app["messages"][app["sel"]]
            if app["demo"] or live is None:
                toast("demo: not sent")
            else:
                live.send_reply(m["prefix"], CANNED[app["pick"]])
            app["screen"] = "feed"


def update():
    handle_input()
    if live is not None:
        try:
            live.step()
        except Exception as error:
            app["status"] = "live: %s" % error
            on_exit()
            globals()["live"] = None
            app["screen"] = "start"
    draw()


run(update)
