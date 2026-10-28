import machine
from badgeware import State

try:
    from aye_arr.nec.remotes.descriptor import RemoteDescriptor
    try:
        # pimoroni/aye_arr 1.0+: the receiver that binds RemoteDescriptors
        from aye_arr.nec import NECRemoteReceiver as Receiver
    except ImportError:
        # 2025 build (what the quest app was written against)
        from aye_arr.nec import NECReceiver as Receiver
    HAVE_IR = True
except ImportError:
    HAVE_IR = False

try:
    from aye_arr.nec import NECSender
except ImportError:
    NECSender = None

IR_TX_PIN = 16
IR_RX_PIN = 17
COOLDOWN_MS = 400
ACK_DELAY_MS = 100
ACK_WAIT_MS = 1000      # after firing, how long a confirmation counts
SELF_MS = 500           # after firing, our own id coming back is a reflection
SAVE_DELAY_MS = 2000    # save stats once they stop changing
SIM_ACK_MS = 150
FLASH_DECAY = 0.93
ACK_FLAG = 128

# Shooter ids are 1..120. A confirmation for shooter N is sent as N + 128.
# The first byte of the hardware id is the same on many boards, so fold all of it.
uid = 0
for b in machine.unique_id():
    uid = (uid * 31 + b) & 0xFFFF
my_id = 1 + uid % 120

BG = color.rgb(13, 17, 23)
GREEN = color.rgb(46, 160, 67)
LIME = color.rgb(211, 250, 55)
RED = color.rgb(248, 81, 73)
WHITE = color.rgb(235, 245, 255)
DIM = color.rgb(139, 148, 158)

screen.font = font.nope

stats = {"tags": 0, "hits": 0}
State.load("ir_tag", stats)

last_shot = -COOLDOWN_MS
ack_until = 0
pending_ack = None
ack_at = 0
sim_ack_at = None
dirty_at = None
streak = 0
last_event = "aim, press Select"
flash = 0.0
flash_color = GREEN
status = ""


def on_code(code, *_):
    # *_ because newer aye_arr passes extra timing args to callbacks
    global pending_ack, ack_at, ack_until, dirty_at, streak, last_event, flash, flash_color

    now = badge.ticks
    if code == my_id:
        # our own shot bouncing back into the receiver
        if now - last_shot < SELF_MS:
            return
    if code < ACK_FLAG:
        stats["hits"] += 1
        dirty_at = now
        pending_ack = code + ACK_FLAG
        ack_at = now + ACK_DELAY_MS
        last_event = "tagged by #%d" % code
        flash, flash_color = 1.0, RED
    elif code - ACK_FLAG == my_id and now <= ack_until:
        # one confirmation per shot, a second badge in the beam is ignored
        ack_until = 0
        stats["tags"] += 1
        dirty_at = now
        streak += 1
        last_event = "hit confirmed"
        flash, flash_color = 1.0, LIME


receiver = None
sender = None

if HAVE_IR:
    class TagRemote(RemoteDescriptor):
        NAME = "TagRemote"
        ADDRESS = 0x47      # quest beacons use 0x45
        BUTTON_CODES = {code: code for code in range(1, 255)}

        def __init__(self):
            super().__init__()

    remote = TagRemote()
    remote.on_known = on_code

    try:
        receiver = Receiver(IR_RX_PIN, 0, 0)
        receiver.bind(remote)
        receiver.start()
    except Exception as e:
        receiver = None
        status = "no rx: " + str(e)

    # Sender is (pin, pio, sm). Same PIO as the receiver, other state machine.
    # aye_arr 1.0.0 refuses a second program on a PIO, so then try PIO 1.
    if NECSender is not None:
        for pio, sm in ((0, 1), (1, 0)):
            try:
                sender = NECSender(IR_TX_PIN, pio, sm)
                sender.start()
                break
            except Exception as e:
                sender = None
                status = "rx only: " + str(e)
    else:
        status = "rx only: no NECSender"
else:
    status = "sim: Up = get hit"


def send(code):
    global status
    if sender is None:
        return False
    try:
        sender.send_addr_cmd(TagRemote.ADDRESS, code)
        return True
    except Exception as e:
        status = "tx: " + str(e)
        return False


def fire(now):
    global last_shot, ack_until, sim_ack_at, last_event, flash, flash_color

    last_shot = now
    if HAVE_IR:
        if not send(my_id):
            last_event = "send failed"
            return
    else:
        sim_ack_at = now + SIM_ACK_MS
    ack_until = now + ACK_WAIT_MS
    last_event = "fired"
    flash, flash_color = 0.5, WHITE


def save_stats():
    global dirty_at
    State.save("ir_tag", stats)
    dirty_at = None


def draw():
    screen.pen = BG
    screen.clear()

    screen.pen = LIME
    screen.text("IR TAG  #%d" % my_id, 4, 4)

    screen.pen = GREEN
    screen.text("tags %d" % stats["tags"], 4, 30)
    if streak >= 2:
        screen.text("streak %d" % streak, 84, 30)
    screen.pen = RED
    screen.text("hit %d" % stats["hits"], 4, 46)

    ready = min(1.0, (badge.ticks - last_shot) / COOLDOWN_MS)
    screen.pen = DIM
    screen.rectangle(4, 70, 80, 6)
    screen.pen = LIME if ready >= 1.0 else DIM
    screen.rectangle(4, 70, int(80 * ready), 6)

    screen.pen = WHITE
    screen.text(last_event, 4, 86)

    screen.pen = DIM if not HAVE_IR else RED
    screen.text(status, 4, 104)

    if flash > 0.05:
        screen.pen = flash_color
        screen.rectangle(0, 0, screen.width, 2)
        screen.rectangle(0, screen.height - 2, screen.width, 2)


def update():
    global pending_ack, ack_until, streak, flash, last_event, sim_ack_at

    if receiver is not None:
        receiver.decode()
    now = badge.ticks

    if badge.pressed(BUTTON_SELECT) and now - last_shot >= COOLDOWN_MS:
        fire(now)

    if not HAVE_IR:
        # no IR in the simulator: fake a confirmation, and Up fakes a hit from badge #7
        if sim_ack_at is not None and now >= sim_ack_at:
            sim_ack_at = None
            on_code(my_id + ACK_FLAG)
        if badge.pressed(BUTTON_UP):
            on_code(7)

    if ack_until and now > ack_until:
        ack_until = 0
        streak = 0
        last_event = "missed"

    if pending_ack is not None and now >= ack_at:
        send(pending_ack)
        pending_ack = None

    if dirty_at is not None and now - dirty_at >= SAVE_DELAY_MS:
        save_stats()

    flash *= FLASH_DECAY
    badge.caselights(flash)
    draw()

    if badge.pressed(BUTTON_BACK):
        if dirty_at is not None:
            save_stats()
        reset()


def on_exit():
    if dirty_at is not None:
        save_stats()
    if receiver is not None:
        receiver.stop()
    if sender is not None:
        sender.stop()


run(update)
