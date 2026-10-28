import machine
from badgeware import State

try:
    from aye_arr.nec import NECReceiver
    from aye_arr.nec.remotes.descriptor import RemoteDescriptor
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
SIM_ACK_MS = 150
FLASH_DECAY = 0.93
ACK_FLAG = 128

# Shooter ids are 1..120. A confirmation for shooter N is sent as N + 128.
my_id = 1 + machine.unique_id()[0] % 120

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
pending_ack = None
ack_at = 0
sim_ack_at = None
last_event = "aim, press Select"
flash = 0.0
flash_color = GREEN
send_error = ""


def on_code(code):
    global pending_ack, ack_at, last_event, flash, flash_color

    if code == my_id:
        # own shot bouncing back into the receiver
        return
    if code < ACK_FLAG:
        stats["hits"] += 1
        State.save("ir_tag", stats)
        pending_ack = code + ACK_FLAG
        ack_at = badge.ticks + ACK_DELAY_MS
        last_event = "tagged by #%d" % code
        flash, flash_color = 1.0, RED
    elif code - ACK_FLAG == my_id:
        stats["tags"] += 1
        State.save("ir_tag", stats)
        last_event = "hit confirmed"
        flash, flash_color = 1.0, LIME


receiver = None
sender = None

if HAVE_IR:
    class TagRemote(RemoteDescriptor):
        NAME = "TagRemote"
        ADDRESS = 0x47
        BUTTON_CODES = {code: code for code in range(1, 255)}

        def __init__(self):
            super().__init__()

    remote = TagRemote()
    remote.on_known = on_code

    receiver = NECReceiver(IR_RX_PIN, 0, 0)
    receiver.bind(remote)
    receiver.start()

    # Constructor args are a guess based on NECReceiver(pin, state_machine, ...).
    # Check the real signature on the badge if this fails.
    if NECSender is not None:
        try:
            sender = NECSender(IR_TX_PIN, 1)
        except Exception as e:
            send_error = str(e)
    else:
        send_error = "no NECSender"
else:
    send_error = "no IR here"


def send(code):
    global send_error
    if sender is None:
        return False
    try:
        sender.send_addr_cmd(TagRemote.ADDRESS, code)
        return True
    except Exception as e:
        send_error = str(e)
        return False


def draw():
    screen.pen = BG
    screen.clear()

    screen.pen = LIME
    screen.text("IR TAG  #%d" % my_id, 4, 4)

    screen.pen = GREEN
    screen.text("tags %d" % stats["tags"], 4, 30)
    screen.pen = RED
    screen.text("hit %d" % stats["hits"], 4, 46)

    ready = min(1.0, (badge.ticks - last_shot) / COOLDOWN_MS)
    screen.pen = DIM
    screen.rectangle(4, 70, 80, 6)
    screen.pen = LIME if ready >= 1.0 else DIM
    screen.rectangle(4, 70, int(80 * ready), 6)

    screen.pen = WHITE
    screen.text(last_event, 4, 86)

    screen.pen = DIM
    if not HAVE_IR:
        screen.text("sim: Up = get hit", 4, 106)
    elif sender is None:
        screen.pen = RED
        screen.text("rx only: " + send_error[:14], 4, 106)

    if flash > 0.05:
        screen.pen = flash_color
        screen.rectangle(0, 0, screen.width, 2)
        screen.rectangle(0, screen.height - 2, screen.width, 2)


def update():
    global last_shot, pending_ack, flash, last_event, sim_ack_at

    if receiver is not None:
        receiver.decode()
    now = badge.ticks

    if badge.pressed(BUTTON_SELECT) and now - last_shot >= COOLDOWN_MS:
        last_shot = now
        if not HAVE_IR:
            last_event = "fired"
            sim_ack_at = now + SIM_ACK_MS
        elif send(my_id):
            last_event = "fired"
        else:
            last_event = "send failed"

    if not HAVE_IR:
        # no IR in the simulator: fake a confirmation, and Up fakes a hit from badge #7
        if sim_ack_at is not None and now >= sim_ack_at:
            sim_ack_at = None
            on_code(my_id + ACK_FLAG)
        if badge.pressed(BUTTON_UP):
            on_code(7)

    if pending_ack is not None and now >= ack_at:
        send(pending_ack)
        pending_ack = None

    flash *= FLASH_DECAY
    badge.caselights(flash)
    draw()

    if badge.pressed(BUTTON_BACK):
        reset()


def on_exit():
    if receiver is not None:
        receiver.stop()


run(update)
