import random

from badgeware import State

HUD_H = 16
GRAVITY = 16384.0
ACCEL = 260.0  # px/s^2 at full tilt
FRICTION = 1.6  # velocity lost per second
BOUNCE = 0.35
MAX_SPEED = 90.0
MAX_DT = 0.05
DEADZONE = 0.06  # ignore tilt this small, about 2 degrees
READY_S = 2.0  # hold steady countdown before each maze
LEVELS = ((9, 7), (13, 9), (17, 11), (19, 13))
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1))

BG = color.rgb(13, 17, 23)
WALL = color.rgb(46, 160, 67)
GOAL = color.rgb(255, 214, 10)
BALL = color.rgb(235, 245, 255)
TEXT = color.rgb(211, 250, 55)
START = color.rgb(30, 54, 40)
GOAL_DIM = color.rgb(150, 120, 10)

PLAY = 0
WIN = 1

screen.font = font.nope

best = {"best_%d" % i: 0.0 for i in range(len(LEVELS))}
State.load("tilt_maze", best)


def clamp(value, low, high):
    return max(low, min(high, value))


def generate(cols, rows):
    """Block maze as rows of 0 (floor) and 1 (wall). cols and rows must be odd."""
    grid = [[1] * cols for _ in range(rows)]
    grid[1][1] = 0
    stack = [(1, 1)]
    while stack:
        x, y = stack[-1]
        options = []
        for dx, dy in NEIGHBOURS:
            nx, ny = x + dx * 2, y + dy * 2
            if 0 < nx < cols - 1 and 0 < ny < rows - 1 and grid[ny][nx]:
                options.append((dx, dy))
        if not options:
            stack.pop()
            continue
        dx, dy = random.choice(options)
        grid[y + dy][x + dx] = 0
        grid[y + dy * 2][x + dx * 2] = 0
        stack.append((x + dx * 2, y + dy * 2))
    return grid


def farthest(grid, start):
    """Floor tile with the longest walk from start (last tile reached by BFS)."""
    seen = {start}
    queue = [start]
    i = 0
    while i < len(queue):
        x, y = queue[i]
        i += 1
        for dx, dy in NEIGHBOURS:
            tile = (x + dx, y + dy)
            if not grid[tile[1]][tile[0]] and tile not in seen:
                seen.add(tile)
                queue.append(tile)
    return queue[-1]


class Tilt:
    def __init__(self):
        self.rest = None
        self.flipped = False

    def recentre(self):
        self.rest = None

    def read(self):
        ax, _, az = badge.imu()[:3]
        flipped = badge.upside_down()
        if flipped != self.flipped:
            # the sensor frame turned 180 degrees, so the old rest pose is stale
            self.flipped = flipped
            self.rest = None
        if self.rest is None:
            self.rest = (ax, az)
        x = -(ax - self.rest[0]) / GRAVITY * 2
        y = (az - self.rest[1]) / GRAVITY * 2
        if flipped:
            x, y = -x, -y
        if abs(x) < DEADZONE:
            x = 0.0
        if abs(y) < DEADZONE:
            y = 0.0
        # d-pad also rolls the ball, which is how you play in the simulator
        push = badge.direction()
        return clamp(x + push.x, -1.0, 1.0), clamp(y + push.y, -1.0, 1.0)


class Level:
    def __init__(self, index):
        self.index = index
        cols, rows = LEVELS[min(index, len(LEVELS) - 1)]
        self.cols = cols
        self.rows = rows
        self.grid = generate(cols, rows)
        self.goal = farthest(self.grid, (1, 1))
        self.tile = min(int(screen.width) // cols, (int(screen.height) - HUD_H) // rows)
        self.ox = (int(screen.width) - cols * self.tile) // 2
        self.oy = HUD_H + (int(screen.height) - HUD_H - rows * self.tile) // 2
        self.r = self.tile * 0.25
        self.x = 1.5 * self.tile
        self.y = 1.5 * self.tile
        self.vx = 0.0
        self.vy = 0.0
        self.elapsed = 0.0
        self.ready = READY_S

    def blocked(self, x, y):
        r = self.r
        t = self.tile
        for ty in range(int((y - r) // t), int((y + r) // t) + 1):
            for tx in range(int((x - r) // t), int((x + r) // t) + 1):
                if self.grid[ty][tx]:
                    return True
        return False

    def step(self, dt, tilt_x, tilt_y):
        self.elapsed += dt
        self.vx += tilt_x * ACCEL * dt
        self.vy += tilt_y * ACCEL * dt
        damp = max(0.0, 1 - FRICTION * dt)
        self.vx *= damp
        self.vy *= damp
        speed = (self.vx * self.vx + self.vy * self.vy) ** 0.5
        if speed > MAX_SPEED:
            self.vx *= MAX_SPEED / speed
            self.vy *= MAX_SPEED / speed

        dx = self.vx * dt
        dy = self.vy * dt
        steps = int(max(abs(dx), abs(dy)) / (self.r * 0.5)) + 1
        for _ in range(steps):
            self.x += dx / steps
            if self.blocked(self.x, self.y):
                self.x -= dx / steps
                self.vx *= -BOUNCE
            self.y += dy / steps
            if self.blocked(self.x, self.y):
                self.y -= dy / steps
                self.vy *= -BOUNCE

    def at_goal(self):
        return (int(self.x // self.tile), int(self.y // self.tile)) == self.goal

    def draw(self):
        t = self.tile
        screen.pen = WALL
        for ty in range(self.rows):
            row = self.grid[ty]
            for tx in range(self.cols):
                if row[tx]:
                    screen.rectangle(self.ox + tx * t, self.oy + ty * t, t, t)

        screen.pen = START
        screen.rectangle(self.ox + t, self.oy + t, t, t)

        gx, gy = self.goal
        screen.pen = GOAL if int(self.elapsed * 2.5) % 2 == 0 else GOAL_DIM
        screen.rectangle(self.ox + gx * t + 1, self.oy + gy * t + 1, t - 2, t - 2)

        screen.pen = BALL
        screen.shape(shape.circle(self.ox + self.x, self.oy + self.y, self.r))


tilt = Tilt()
glow = 0.0
level_index = 0
level = Level(level_index)
mode = PLAY


def best_key():
    return "best_%d" % min(level_index, len(LEVELS) - 1)


def draw_hud(text):
    screen.pen = TEXT
    screen.text(text, 3, 3)


def update():
    global level_index, level, mode, glow

    screen.pen = BG
    screen.clear()
    dt = min(badge.ticks_delta / 1000, MAX_DT)
    glow = max(0.0, glow - dt * 1.5)
    badge.caselights(glow)

    if mode == PLAY:
        if level.ready > 0:
            # hold the badge the way you want to play, Select skips the wait
            level.ready -= dt
            if badge.pressed(BUTTON_SELECT) or level.ready <= 0:
                level.ready = 0
                tilt.recentre()
            draw_hud("L%d  hold steady %d" % (level_index + 1, int(level.ready) + 1))
        else:
            if badge.pressed(BUTTON_SELECT):
                tilt.recentre()
            tilt_x, tilt_y = tilt.read()
            level.step(dt, tilt_x, tilt_y)

            if level.at_goal():
                if best[best_key()] == 0 or level.elapsed < best[best_key()]:
                    best[best_key()] = level.elapsed
                    State.save("tilt_maze", best)
                glow = 1.0
                mode = WIN

            top = best[best_key()]
            draw_hud("L%d  %.1fs  best %s" % (level_index + 1, level.elapsed, "%.1f" % top if top else "-"))
        level.draw()
    else:
        level.draw()
        screen.pen = BG
        screen.rectangle(20, 36, screen.width - 40, 40)
        screen.pen = GOAL
        screen.text("Done in %.1fs" % level.elapsed, 32, 42)
        screen.pen = TEXT
        screen.text("SELECT for next", 32, 58)
        draw_hud("L%d cleared" % (level_index + 1))
        if badge.pressed(BUTTON_SELECT):
            level_index += 1
            level = Level(level_index)
            tilt.recentre()
            mode = PLAY

    if badge.pressed(BUTTON_BACK):
        badge.caselights(0)
        reset()


run(update)
