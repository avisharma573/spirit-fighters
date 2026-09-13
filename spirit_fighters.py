"""
SPIRIT FIGHTERS  —  Neon Citadel Stadium
A 2D tactical stick-fighter arena built on pygame.

Controls
  Menu    A/D or <-/->  select spirit      ENTER fight      S shop      ESC quit
  Shop    arrows move cursor               ENTER equip/unequip          ESC back
  Fight   WASD move   J / LMB punch   K / RMB kick   1-4 abilities   SPACE special
          ESC back to menu

Run:  python3 spirit_fighters.py
Self-test (headless):  SF_SELFTEST=1 python3 spirit_fighters.py
"""

import os
import json
import math
import random
import time
from collections import deque

import pygame

# --------------------------------------------------------------------------
# boot
# --------------------------------------------------------------------------
SELFTEST = bool(os.environ.get("SF_SELFTEST"))
if SELFTEST:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

try:
    pygame.mixer.pre_init(44100, -16, 1, 512)
except Exception:
    pass
pygame.init()

W, H = 1280, 720
display = pygame.display.set_mode((W, H))
pygame.display.set_caption("Spirit Fighters — Neon Citadel Stadium")
# Everything is drawn to this offscreen buffer so the whole frame can be
# shaken on impact before it reaches the window.
screen = pygame.Surface((W, H)).convert()
clock = pygame.time.Clock()

F = pygame.font.SysFont("arial", 20, 1)
S = pygame.font.SysFont("arial", 15, 1)
B = pygame.font.SysFont("arial", 55, 1)
M = pygame.font.SysFont("arial", 28, 1)
SM = pygame.font.SysFont("arial", 13, 1)
XL = pygame.font.SysFont("arial", 96, 1)

WHITE = (245, 247, 250); BLACK = (5, 7, 10);    GOLD = (255, 200, 65)
GREEN = (70, 220, 125);  RED = (235, 60, 75);   CYAN = (65, 220, 240)
BLUE = (65, 125, 235);   GREY = (80, 92, 108);  DIM = (150, 165, 182)
SKIN = (226, 190, 162)

# --------------------------------------------------------------------------
# CONFIG
# Every tunable value in the game lives here under a named key. Sections are
# added as each phase lands; nothing outside this block should hold a magic
# number you would reasonably want to dial.
# --------------------------------------------------------------------------
class Cfg(dict):
    """A dict you can also reach with dots, so CFG.arena.ground_top works."""
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


CFG = Cfg(
    game=Cfg(
        dev_mode=True,          # every shop item is free while the game is unreleased
        round_time=90.0,        # seconds before the match goes to a decision
        intro_time=1.3,         # "FIGHT!" hold before the AI engages
        ko_time=1.5,            # slow-motion hold after a knockout
        ko_timescale=0.30,      # how slowly that hold runs
        hitstop_timescale=0.25,  # time dilation during an impact freeze
    ),
    arena=Cfg(
        ground_top=415,         # nearest / furthest the feet may stand
        ground_bottom=578,
        margin_x=120,           # walkable inset from the screen edge
        depth_squash=0.62,      # vertical movement is foreshortened by this
        scale_near=0.98,        # figure scale at ground_top ...
        scale_range=0.30,       # ... growing by this much at ground_bottom
    ),
    glow=Cfg(
        alpha=40,               # peak additive alpha at a glow's centre
        falloff=2.6,            # higher = tighter core, softer edge
        radius_step=3,          # cache-key quantisation keeps the cache bounded
        color_step=12,
        cache_max=192,
    ),
    debug=Cfg(
        overlay=False,          # F3 toggles the frame-time readout
        samples=90,             # rolling window for the ms/frame average
    ),
)

# --------------------------------------------------------------------------
# audio — synthesised at boot, silently skipped if numpy/audio is unavailable
# --------------------------------------------------------------------------
SND = {}


def _build_sounds():
    import numpy as np
    if pygame.mixer.get_init() is None:
        pygame.mixer.init(44100, -16, 1, 512)
    rate = pygame.mixer.get_init()[0]

    def bake(samples, vol):
        a = np.clip(samples, -1.0, 1.0) * vol
        return pygame.sndarray.make_sound((a * 32767).astype(np.int16))

    def env(n, attack=0.004, power=2.2):
        t = np.linspace(0.0, 1.0, n, False)
        a = np.clip(t / max(attack, 1e-5), 0, 1)
        return a * (1.0 - t) ** power

    def tone(freq_a, freq_b, dur, noise=0.0, power=2.2, vol=0.25):
        n = int(rate * dur)
        t = np.linspace(0.0, dur, n, False)
        f = np.linspace(freq_a, freq_b, n)
        wave = np.sin(2 * np.pi * np.cumsum(f) / rate)
        if noise:
            wave = wave * (1 - noise) + np.random.uniform(-1, 1, n) * noise
        return bake(wave * env(n, power=power), vol)

    SND["punch"] = tone(190, 70, 0.16, noise=0.55, vol=0.30)
    SND["kick"] = tone(150, 48, 0.24, noise=0.45, vol=0.34)
    SND["whoosh"] = tone(900, 260, 0.16, noise=0.80, power=1.4, vol=0.13)
    SND["hit"] = tone(420, 120, 0.13, noise=0.35, vol=0.22)
    SND["cast"] = tone(300, 880, 0.22, noise=0.05, power=1.6, vol=0.18)
    SND["shot"] = tone(760, 300, 0.14, noise=0.15, vol=0.16)
    SND["boom"] = tone(130, 40, 0.45, noise=0.70, power=1.6, vol=0.36)
    SND["ult"] = tone(180, 1100, 0.70, noise=0.10, power=1.1, vol=0.32)
    SND["heal"] = tone(520, 980, 0.30, noise=0.0, power=1.6, vol=0.16)
    SND["ko"] = tone(320, 60, 0.80, noise=0.25, power=1.2, vol=0.34)
    SND["ui"] = tone(700, 700, 0.05, noise=0.0, power=3.0, vol=0.12)
    SND["equip"] = tone(520, 1040, 0.14, noise=0.0, power=2.0, vol=0.16)


try:
    _build_sounds()
except Exception:
    SND = {}


def sfx(name, vol=1.0):
    s = SND.get(name)
    if s:
        try:
            s.set_volume(vol)
            s.play()
        except Exception:
            pass


# --------------------------------------------------------------------------
# spirit roster
#   name: (class, body colour, accent colour, hp, speed, abilities, ultimate)
#   ability: (name, cooldown, damage, kind)
# --------------------------------------------------------------------------
SP = {
    "Earth": ("TANK / CONTROL", (108, 78, 48), (225, 175, 80), 340, 205, [
        ("Rock Punch", 1.5, 22, "melee"), ("Stone Throw", 2.2, 28, "shot"),
        ("Stone Shield", 3.5, 0, "shield"), ("Earthquake", 4.2, 35, "area"),
    ], ("Earth Titan", 80)),
    "Fire": ("ASSAULT / DOT", (150, 44, 26), (255, 140, 45), 250, 245, [
        ("Fire Punch", 1.2, 24, "melee"), ("Fireball", 1.8, 30, "shot"),
        ("Flame Dash", 2.4, 32, "dash"), ("Burning Ground", 3.5, 18, "area"),
    ], ("Inferno", 24)),
    "Police": ("CONTROL / DISABLE", (30, 62, 130), (80, 190, 255), 270, 250, [
        ("Baton Strike", 1.2, 23, "melee"), ("Taser", 1.9, 20, "stun"),
        ("Handcuff Trap", 3.0, 15, "root"), ("Arrest", 4.2, 28, "arrest"),
    ], ("SWAT Raid", 78)),
    "Gambler": ("TRICKSTER / RNG", (108, 34, 120), (255, 210, 70), 245, 250, [
        ("Lucky Punch", 1.1, 20, "melee"), ("Dice Blast", 1.7, 25, "random"),
        ("Jackpot", 3.0, 45, "random"), ("Risky Roll", 2.5, 0, "buff"),
    ], ("House Always Wins", 90)),
    "Arch Angel": ("MYTHIC / HYBRID", (196, 158, 58), (255, 245, 170), 285, 265, [
        ("Light Blade", 1.2, 25, "melee"), ("Light Dash", 2.0, 32, "dash"),
        ("Holy Shield", 3.2, 0, "shield"), ("Healing Light", 3.0, 25, "heal"),
    ], ("Divine Judgment", 95)),
    "Demonic": ("ASSASSIN / LIFESTEAL", (82, 20, 100), (240, 45, 105), 255, 275, [
        ("Demon Claw", 1.0, 26, "melee"), ("Soul Shot", 1.7, 28, "shot"),
        ("Demon Flight", 2.6, 0, "buff"), ("Soul Steal", 3.5, 38, "life"),
    ], ("Demon Rage", 88)),
    "Archer": ("RANGED / PRECISION", (28, 108, 58), (120, 240, 120), 235, 235, [
        ("Quick Shot", 0.9, 20, "shot"), ("Triple Arrow", 2.1, 34, "triple"),
        ("Piercing Arrow", 2.7, 40, "shot"), ("Explosive Arrow", 3.4, 32, "area"),
    ], ("Rain of Arrows", 20)),
    "Healer": ("SUPPORT / SUSTAIN", (34, 150, 96), (150, 255, 205), 245, 225, [
        ("Staff Strike", 1.2, 18, "melee"), ("Heal Pulse", 2.3, 28, "heal"),
        ("Healing Zone", 3.3, 16, "hzone"), ("Regeneration", 4.0, 45, "regen"),
    ], ("Full Restore", 90)),
    "Racer": ("SPEED / HIT & RUN", (24, 118, 190), (80, 235, 255), 230, 310, [
        ("Turbo Punch", 1.0, 22, "melee"), ("Turbo Dash", 1.6, 30, "dash"),
        ("Spin Attack", 2.3, 32, "spin"), ("Nitro Strike", 3.2, 42, "dash"),
    ], ("Sonic Speed", 80)),
}
names = list(SP)

# --------------------------------------------------------------------------
# shop — every entry is a real, stacking modifier applied to your fighter
# --------------------------------------------------------------------------
SHOP = [
    ("COMBAT", "Shockwave Punch", "Basic attacks hit 15% harder.", {"dmg": 1.15}),
    ("COMBAT", "Iron Fists", "+10% damage and +30 max health.", {"dmg": 1.10, "hp": 30}),
    ("COMBAT", "Counter Guard", "Take 12% less damage.", {"armor": 0.88}),
    ("COMBAT", "Berserker Mark", "+25% damage but -45 max health.", {"dmg": 1.25, "hp": -45}),
    ("COMBAT", "Vampiric Edge", "Heal 20% of the damage you deal.", {"lifesteal": 0.20}),
    ("COMBAT", "Executioner", "+20% ability damage.", {"ability_dmg": 1.20}),
    ("MOBILITY", "Swift Boots", "+12% movement speed.", {"speed": 1.12}),
    ("MOBILITY", "Air Dash", "+20% movement speed.", {"speed": 1.20}),
    ("MOBILITY", "Phase Step", "Begin every match with a 4s shield.", {"shield": 4.0}),
    ("RANGED", "Energy Focus", "Projectiles fly 35% faster.", {"proj": 1.35}),
    ("RANGED", "Boom Shot", "+15% ability damage.", {"ability_dmg": 1.15}),
    ("RANGED", "Twin Blasts", "Ability cooldowns 12% shorter.", {"cdr": 0.88}),
    ("POWER", "Power Surge", "Ability cooldowns 18% shorter.", {"cdr": 0.82}),
    ("POWER", "Overdrive", "Start the match with 35% special charge.", {"ult": 35}),
    ("POWER", "Second Wind", "+70 max health.", {"hp": 70}),
    ("SPECIAL", "Meteor Heart", "+15% damage and +40 max health.", {"dmg": 1.15, "hp": 40}),
    ("SPECIAL", "Gravity Core", "Take 15% less damage.", {"armor": 0.85}),
    ("SPECIAL", "Spirit Nova", "Special charges 30% faster.", {"ult_rate": 1.30}),
]

SAVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spirit_fighters_save.json")
equipped = set()


def load_save():
    global equipped
    try:
        with open(SAVE) as fh:
            data = json.load(fh)
        equipped = {i for i in data.get("equipped", []) if isinstance(i, int) and 0 <= i < len(SHOP)}
    except Exception:
        equipped = set()


def write_save():
    try:
        with open(SAVE, "w") as fh:
            json.dump({"equipped": sorted(equipped)}, fh)
    except Exception:
        pass


def loadout():
    """Collapse every equipped item into one multiplier bundle."""
    b = {"dmg": 1.0, "ability_dmg": 1.0, "hp": 0, "armor": 1.0, "lifesteal": 0.0,
         "speed": 1.0, "shield": 0.0, "ult": 0.0, "ult_rate": 1.0, "cdr": 1.0, "proj": 1.0}
    for i in equipped:
        for k, v in SHOP[i][3].items():
            if k in ("hp", "ult", "lifesteal"):
                b[k] += v
            elif k == "shield":
                b[k] = max(b[k], v)
            else:
                b[k] *= v
    return b


load_save()

# --------------------------------------------------------------------------
# global state
# --------------------------------------------------------------------------
particles = []; shots = []; zones = []; floaters = []
state = "menu"; pick = 0; p = e = None; result = ""
timer = CFG.game.round_time; intro = 0.0; ko_timer = 0.0
shop_sel = 0
shake = 0.0; flash = 0.0
banner = ""; banner_t = 0.0


# --------------------------------------------------------------------------
# small drawing helpers
# --------------------------------------------------------------------------
def txt(t, pos, font=F, col=WHITE, center=False, surf=None):
    surf = surf or screen
    r = font.render(str(t), True, col)
    surf.blit(r, r.get_rect(center=pos) if center else r.get_rect(topleft=pos))


def ipt(a):
    return (int(a[0]), int(a[1]))


_glow_cache = {}


def glow_surf(radius, color):
    """Cached radial glow.

    The key is quantised: callers pass continuously-varying colours (a fading
    zone, a pulsing aura), and an exact key meant a fresh bake plus a new cache
    entry every single frame — an unbounded leak. Snapping radius and colour to
    a step makes the key set finite and the cache a real cache.
    """
    step = CFG.glow.color_step
    radius = max(2, int(radius) // CFG.glow.radius_step * CFG.glow.radius_step)
    key = (radius,
           int(color[0]) // step * step,
           int(color[1]) // step * step,
           int(color[2]) // step * step)
    g = _glow_cache.get(key)
    if g is None:
        if len(_glow_cache) >= CFG.glow.cache_max:
            _glow_cache.clear()
        g = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        for r in range(radius, 0, -1):
            a = int(CFG.glow.alpha * (1 - r / radius) ** CFG.glow.falloff)
            if a:
                pygame.draw.circle(g, (key[1], key[2], key[3], a), (radius, radius), r)
        _glow_cache[key] = g
    return g


def glow(x, y, radius, color, surf=None):
    g = glow_surf(radius, color)
    r = g.get_width() // 2          # the cache may have rounded the radius
    (surf or screen).blit(g, (x - r, y - r), special_flags=pygame.BLEND_RGBA_ADD)


def limb(a, b, w, col, surf=None):
    """A rounded capsule — the trick that makes stick limbs read as bodies."""
    surf = surf or screen
    pygame.draw.line(surf, col, a, b, w)
    pygame.draw.circle(surf, col, ipt(a), w // 2)
    pygame.draw.circle(surf, col, ipt(b), w // 2)


def shade(col, f):
    return (max(0, min(255, int(col[0] * f))),
            max(0, min(255, int(col[1] * f))),
            max(0, min(255, int(col[2] * f))))


def burst(x, y, c, n=15, power=180, size=5, grav=520, life=0.55):
    for _ in range(n):
        a = random.random() * math.tau
        v = random.uniform(power * 0.25, power)
        lf = life * (0.5 + random.random() * 0.8)
        particles.append([x, y, math.cos(a) * v, math.sin(a) * v, lf, lf,
                          c, size * (0.55 + random.random() * 0.7), grav])


def smoke(x, y, c, n=8, power=60):
    for _ in range(n):
        a = random.random() * math.tau
        v = random.uniform(10, power)
        lf = 0.7 + random.random() * 0.7
        particles.append([x, y, math.cos(a) * v, math.sin(a) * v - 30, lf, lf,
                          c, 7 + random.random() * 6, -40])


def add_shake(v):
    global shake
    shake = min(26.0, shake + v)


def add_flash(v):
    global flash
    flash = min(150.0, flash + v)


def dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def say(text, secs=1.1):
    global banner, banner_t
    banner, banner_t = text, secs


# --------------------------------------------------------------------------
# the stadium — baked once so nothing flickers and the frame stays cheap
# --------------------------------------------------------------------------
PITCH = pygame.Rect(20, 195, W - 40, 470)      # ground ellipse
# derived from CFG so the hot paths below read cleanly; edit CFG.arena, not these
GROUND_TOP, GROUND_BOTTOM = CFG.arena.ground_top, CFG.arena.ground_bottom
MARGIN_X = CFG.arena.margin_x


def build_arena():
    bg = pygame.Surface((W, H)).convert()
    rng = random.Random(20240913)

    # night sky above the bowl
    for y in range(0, 240):
        t = y / 240
        pygame.draw.line(bg, (int(6 + t * 8), int(9 + t * 13), int(18 + t * 22)), (0, y), (W, y))
    for _ in range(90):                                    # stars through the open roof
        sx, sy = rng.randint(0, W), rng.randint(0, 80)
        v = rng.randint(60, 130)
        pygame.draw.circle(bg, (v, v, v + 20), (sx, sy), 1)

    # roof trusses
    for x in range(-140, W + 160, 105):
        pygame.draw.line(bg, (36, 45, 60), (x, 40), (x + 120, 150), 3)
        pygame.draw.line(bg, (26, 33, 45), (x + 120, 150), (x + 120, 190), 2)
    pygame.draw.rect(bg, (17, 22, 33), (0, 150, W, 44))

    # crowd — three tiers, each darker with distance, a few team-colour blocks
    tiers = [(196, 3, (26, 32, 44)), (154, 3, (20, 25, 36)), (118, 2, (15, 19, 28))]
    for base_y, rows, seat in tiers:
        pygame.draw.rect(bg, seat, (0, base_y - rows * 13 - 4, W, rows * 13 + 8))
        for r in range(rows):
            yy = base_y - r * 13
            for x in range(-6, W + 10, 8):
                if rng.random() < 0.14:
                    continue
                v = max(8, rng.randint(24, 52) - r * 5)
                col = (v, v + 3, v + 11)
                if rng.random() < 0.022:
                    col = rng.choice([(104, 44, 44), (44, 74, 116), (118, 100, 46), (46, 104, 76)])
                pygame.draw.rect(bg, col, (x, yy + rng.randint(-2, 1), 5, 5))
    pygame.draw.rect(bg, (9, 12, 19), (0, 200, W, 16))     # shadow under the stands

    # the pitch: concentric bands read as mown rings under stadium light
    for k in range(9):
        f = 1 - k * 0.105
        tone = 1.0 + (0.16 if k % 2 else 0.0)
        col = shade((17, 40, 33), tone + (8 - k) * 0.02)
        rect = pygame.Rect(0, 0, int(PITCH.w * f), int(PITCH.h * f))
        rect.center = (PITCH.centerx, PITCH.centery)
        pygame.draw.ellipse(bg, col, rect)

    # markings
    line = (74, 122, 140)
    pygame.draw.ellipse(bg, line, PITCH, 4)
    inner = PITCH.inflate(-150, -110)
    pygame.draw.ellipse(bg, shade(line, 0.6), inner, 2)
    pygame.draw.circle(bg, line, (W // 2, PITCH.centery), 118, 2)
    pygame.draw.circle(bg, line, (W // 2, PITCH.centery), 40, 2)
    pygame.draw.circle(bg, shade(line, 0.8), (W // 2, PITCH.centery), 5)
    pygame.draw.line(bg, line, (W // 2, PITCH.top + 16), (W // 2, PITCH.bottom - 16), 2)

    # player tunnels at both ends — arched mouths cut into the lower stand,
    # with a warm interior light, plus the hoardings that fence them off
    for tx in (36, W - 116):
        mouth = pygame.Rect(tx, 296, 80, 150)
        pygame.draw.rect(bg, (7, 9, 14), mouth, border_radius=38)          # opening
        for i in range(5):                                                 # depth falloff
            inner = mouth.inflate(-10 - i * 9, -10 - i * 9)
            inner.bottom = mouth.bottom - 4
            v = 10 + i * 5
            pygame.draw.rect(bg, (v + 6, v + 3, v), inner, border_radius=26)
        pygame.draw.rect(bg, (44, 56, 72), mouth, 4, border_radius=38)     # frame
        pygame.draw.rect(bg, (74, 92, 116), (tx + 6, 290, 68, 8), border_radius=3)
        # barrier rail in front of the tunnel
        for yy in (452, 466):
            pygame.draw.line(bg, (52, 64, 82), (tx - 6, yy), (tx + 86, yy), 3)
        for xx in range(tx - 4, tx + 88, 21):
            pygame.draw.line(bg, (40, 50, 66), (xx, 448), (xx, 470), 3)

    # floodlight towers (the lamp glow itself is animated at runtime)
    for x in (96, W - 108):
        pygame.draw.rect(bg, (34, 42, 54), (x, 112, 14, 100), border_radius=4)
        pygame.draw.rect(bg, (44, 54, 68), (x - 14, 96, 42, 20), border_radius=5)

    # perimeter advertising boards
    pygame.draw.rect(bg, (14, 20, 30), (0, 664, W, 10))
    return bg


def build_vignette(depth=170, peak=140):
    v = pygame.Surface((W, H), pygame.SRCALPHA)
    for i in range(depth):
        c = (0, 0, 0, int(peak * (1 - i / depth) ** 2.1))
        pygame.draw.line(v, c, (0, i), (W, i))
        pygame.draw.line(v, c, (0, H - 1 - i), (W, H - 1 - i))
        pygame.draw.line(v, c, (i, 0), (i, H))
        pygame.draw.line(v, c, (W - 1 - i, 0), (W - 1 - i, H))
    return v


ARENA = build_arena()
# The vignette is static and sits *under* the characters, so it is baked into
# the arena once instead of being alpha-blitted every frame (measured 0.43 ms —
# 43% of the whole frame). Phase D moves vignetting into the post chain, where
# it will correctly darken the characters too; this bake comes back out then.
ARENA.blit(build_vignette(), (0, 0))


def arena(t):
    screen.blit(ARENA, (0, 0))
    # floodlights breathe very slightly, like real arc lamps
    for x in (103, W - 101):
        b = 0.88 + 0.12 * math.sin(t * 1.7 + x)
        for j in range(3):
            cx, cy = x - 8 + j * 8, 104
            pygame.draw.circle(screen, shade((255, 246, 216), b), (cx, cy), 3)
            glow(cx, cy, int(16 * b), (120, 118, 100))
    # LED perimeter running colour along the boards
    for i, x in enumerate(range(40, W - 40, 46)):
        ph = (math.sin(t * 2.6 - i * 0.35) + 1) * 0.5
        c = (int(14 + 40 * ph), int(40 + 120 * ph), int(60 + 150 * ph))
        pygame.draw.rect(screen, c, (x, 664, 30, 9), border_radius=3)


# --------------------------------------------------------------------------
# world objects
# --------------------------------------------------------------------------
class FloatingText:
    def __init__(self, x, y, s, c=WHITE, size=None):
        self.x = x; self.y = y; self.s = s; self.c = c
        self.life = 0.85; self.font = size or SM
        self.vx = random.uniform(-18, 18)

    def up(self, dt):
        self.y -= 42 * dt
        self.x += self.vx * dt
        self.life -= dt

    def draw(self):
        a = max(0.0, min(1.0, self.life / 0.5))
        txt(self.s, (self.x, self.y), self.font, shade(self.c, 0.35 + 0.65 * a), True)


class Shot:
    """Real projectile: launched with a velocity, only lightly guided, so it
    can genuinely miss a fighter who moves out of the way."""

    def __init__(self, owner, target, damage, color, speed=700, homing=2.2, size=7):
        self.o = owner; self.t = target; self.damage = damage; self.c = color
        self.x = owner.x + owner.facing * 26
        self.y = owner.y - 108 * owner.scale()
        self.homing = homing; self.size = size
        self.life = 1.7
        dx = target.x - self.x
        dy = (target.y - 105) - self.y
        q = math.hypot(dx, dy) or 1
        spread = random.uniform(-0.10, 0.10)
        ca, sa = math.cos(spread), math.sin(spread)
        vx, vy = dx / q * speed, dy / q * speed
        self.vx = vx * ca - vy * sa
        self.vy = vx * sa + vy * ca
        self.trail = []

    def up(self, dt):
        if not self.t.alive:
            self.life = 0
            return
        tx, ty = self.t.x, self.t.y - 100
        dx, dy = tx - self.x, ty - self.y
        q = math.hypot(dx, dy) or 1
        sp = math.hypot(self.vx, self.vy) or 1
        self.vx += (dx / q * sp - self.vx) * min(1, self.homing * dt)
        self.vy += (dy / q * sp - self.vy) * min(1, self.homing * dt)
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.trail.append((self.x, self.y))
        if len(self.trail) > 7:
            self.trail.pop(0)
        if q < 30:
            self.t.hit(self.damage)
            if self.o.lifesteal:
                self.o.heal(self.damage * self.o.lifesteal)
            self.t.knock(1 if self.vx > 0 else -1, 9)
            burst(self.x, self.y, self.c, 16, 170, 5)
            add_shake(2.5)
            sfx("hit", 0.6)
            self.life = 0
            return
        self.life -= dt
        if self.life <= 0:
            burst(self.x, self.y, self.c, 5, 60, 3)

    def draw(self):
        for i, (tx, ty) in enumerate(self.trail):
            r = max(1, int(self.size * (i + 1) / len(self.trail) * 0.8))
            pygame.draw.circle(screen, shade(self.c, 0.35 + 0.5 * i / 7), ipt((tx, ty)), r)
        glow(self.x, self.y, self.size * 4, self.c)
        pygame.draw.circle(screen, self.c, ipt((self.x, self.y)), self.size)
        pygame.draw.circle(screen, WHITE, ipt((self.x, self.y)), max(1, self.size - 4))


class Zone:
    """Ground effect with a telegraph window — you get a moment to leave."""

    def __init__(self, x, y, r, c, life, damage, target, heal=False, warn=0.45):
        self.x = x; self.y = y; self.r = r; self.c = c
        self.life = life; self.damage = damage; self.t = target
        self.heal = heal; self.tick = 0.0; self.warn = warn
        self.born = 0.0

    def up(self, dt):
        self.born += dt
        if self.warn > 0:
            self.warn -= dt
            if self.warn <= 0:                      # the impact moment
                burst(self.x, self.y, self.c, 26, 230, 6)
                smoke(self.x, self.y, shade(self.c, 0.5), 10)
                add_shake(6 if not self.heal else 0)
                if not self.heal:
                    sfx("boom", 0.7)
            return
        self.life -= dt
        self.tick -= dt
        if self.tick <= 0 and self.t.alive and math.hypot(self.t.x - self.x, self.t.y - self.y) < self.r:
            if self.heal:
                self.t.heal(self.damage)
            else:
                self.t.hit(self.damage)
                add_shake(1.2)
            self.tick = 0.55
        if random.random() < dt * 22 and self.life > 0:
            a = random.random() * math.tau
            rr = self.r * math.sqrt(random.random())
            particles.append([self.x + math.cos(a) * rr, self.y + math.sin(a) * rr * 0.45,
                              0, -random.uniform(20, 60), 0.6, 0.6, self.c, 4, -60])

    def _ellipse(self, r, width, col):
        rect = pygame.Rect(0, 0, int(r * 2), int(r * 0.9))
        rect.center = (int(self.x), int(self.y))
        pygame.draw.ellipse(screen, col, rect, width)

    def draw(self):
        if self.warn > 0:
            k = 1 - max(0.0, self.warn) / 0.45
            blink = 0.45 + 0.55 * abs(math.sin(self.born * 22))
            self._ellipse(self.r * (0.35 + 0.65 * k), 3, shade(self.c, blink))
            self._ellipse(self.r, 2, shade(self.c, 0.35 * blink))
            return
        pulse = math.sin(pygame.time.get_ticks() / 180) * 4
        fade = max(0.25, min(1.0, self.life))
        self._ellipse(self.r + pulse, 3, shade(self.c, fade))
        self._ellipse(self.r * 0.62, 1, shade(self.c, fade * 0.7))
        glow(self.x, self.y, int(self.r * 1.1), shade(self.c, 0.09 * fade))


# --------------------------------------------------------------------------
# fighter
# --------------------------------------------------------------------------
class Fighter:
    def __init__(self, n, x, y, player):
        self.n = n; self.x = float(x); self.y = float(y); self.player = player
        (self.cls, self.c, self.b, self.maxhp, self.speed,
         self.abilities, self.ultinfo) = SP[n]
        self.hp = float(self.maxhp)
        self.ult = 30.0 if player else 8.0
        self.cd = [0.0] * 4
        self.cdmax = [a[1] for a in self.abilities]
        self.atk = 0.0; self.atk_dur = 0.3; self.atype = "idle"
        self.anim = random.random() * 4
        self.facing = 1 if player else -1
        self.stun = 0.0; self.root = 0.0; self.shield = 0.0; self.buff = 0.0
        self.alive = True; self.attack_landed = False
        self.combo = 0; self.combo_t = 0.0
        self.hitstop = 0.0; self.flinch = 0.0
        self.moving = False; self.px = self.x; self.py = self.y
        self.kx = 0.0                                    # knockback velocity
        # modifiers (the shop writes these)
        self.dmg_mult = 1.0; self.ability_mult = 1.0; self.armor = 1.0
        self.lifesteal = 0.0; self.cdr = 1.0; self.ult_rate = 1.0; self.proj = 1.0

    # ---- geometry -------------------------------------------------------
    def scale(self):
        """Fighters further up the pitch stand smaller — cheap real depth."""
        return CFG.arena.scale_near + ((self.y - GROUND_TOP) /
                (GROUND_BOTTOM - GROUND_TOP)) * CFG.arena.scale_range

    # ---- damage ---------------------------------------------------------
    def hit(self, damage):
        if not self.alive:
            return
        if self.shield > 0:
            damage *= 0.45
        damage *= self.armor
        self.hp = max(0.0, self.hp - damage)
        self.ult = min(100.0, self.ult + damage * 0.12)
        self.flinch = 0.16
        floaters.append(FloatingText(self.x, self.y - 118, f"-{int(damage)}", RED))
        burst(self.x, self.y - 90 * self.scale(), self.b, 9, 130, 4)
        if self.player:
            add_flash(22)
        if self.hp <= 0:
            self.alive = False
            burst(self.x, self.y - 80, self.b, 55, 300, 7)
            smoke(self.x, self.y - 60, (70, 70, 80), 14)
            add_shake(14)
            sfx("ko")

    def heal(self, amount):
        if not self.alive:
            return
        self.hp = min(self.maxhp, self.hp + amount)
        floaters.append(FloatingText(self.x, self.y - 118, f"+{int(amount)}", GREEN))
        burst(self.x, self.y - 80, GREEN, 10, 90, 4, grav=-120)
        sfx("heal", 0.5)

    def deal(self, t, damage, ability=False):
        d = damage * self.dmg_mult * (self.ability_mult if ability else 1.0)
        t.hit(d)
        if self.lifesteal > 0:
            self.heal(d * self.lifesteal)
        return d

    def knock(self, direction, force):
        self.kx += direction * force

    # ---- movement -------------------------------------------------------
    def move(self, dx, dy, dt):
        if self.stun > 0 or self.root > 0 or not self.alive:
            return
        q = math.hypot(dx, dy)
        if q:
            sp = self.speed * (1.38 if self.buff > 0 else 1.0)
            if self.atk > 0:
                sp *= 0.55                     # committed to a strike, but not frozen
            self.x += dx / q * sp * dt
            self.y += dy / q * sp * dt * CFG.arena.depth_squash  # seen at an angle
            if abs(dx) > 0 and self.atk <= 0:
                self.facing = 1 if dx > 0 else -1
        self.clamp()

    def clamp(self):
        self.x = max(MARGIN_X, min(W - MARGIN_X, self.x))
        self.y = max(GROUND_TOP, min(GROUND_BOTTOM, self.y))

    # ---- attacking ------------------------------------------------------
    def begin_attack(self, kind="punch"):
        if self.atk > 0 or self.stun > 0 or not self.alive:
            return False
        self.atk_dur = 0.30 if kind == "punch" else 0.42
        self.atk = self.atk_dur
        self.atype = kind
        self.attack_landed = False
        self.ult = min(100.0, self.ult + 3)
        sfx("whoosh", 0.5)
        return True

    def attack(self, t, kind="punch"):
        if self.begin_attack(kind):
            self.resolve_basic(t)

    def resolve_basic(self, t):
        """Hit detection runs every frame of the strike against the
        opponent's live position and a facing cone — no scripted hits."""
        if self.atk <= 0 or self.attack_landed or not t.alive or not self.alive:
            return
        phase = 1 - self.atk / self.atk_dur
        if not (0.22 < phase < 0.72):          # only the extended frames connect
            return
        dx = t.x - self.x
        dy = t.y - self.y
        front = dx * self.facing
        reach = (96 if self.atype == "punch" else 124) * self.scale()
        if -22 < front < reach and abs(dy) < 46:
            self.attack_landed = True
            base = 13 if self.atype == "punch" else 18
            if self.combo_t > 0:
                base *= 1 + min(0.35, self.combo * 0.07)
            self.deal(t, base)
            self.combo += 1
            self.combo_t = 1.6
            self.hitstop = 0.05 if self.atype == "punch" else 0.08
            t.knock(self.facing, 10 if self.atype == "punch" else 18)
            hx = self.x + self.facing * reach * 0.8
            hy = self.y - (100 if self.atype == "punch" else 70) * self.scale()
            burst(hx, hy, self.b, 14, 160, 5)
            glow(hx, hy, 40, self.b)
            add_shake(3 if self.atype == "punch" else 5)
            sfx("punch" if self.atype == "punch" else "kick", 0.8)

    # ---- abilities ------------------------------------------------------
    def ability(self, i, t):
        if i < 0 or i > 3 or self.cd[i] > 0 or self.stun > 0 or self.atk > 0 or not self.alive:
            return
        name, cd, damage, k = self.abilities[i]
        self.cdmax[i] = cd * self.cdr
        self.cd[i] = self.cdmax[i]
        self.atk_dur = 0.42 if k in ("melee", "spin") else 0.34
        self.atk = self.atk_dur
        self.atype = "kick" if ("Kick" in name or k == "spin") else ("punch" if k == "melee" else "cast")
        self.attack_landed = False
        self.ult = min(100.0, self.ult + 7)
        floaters.append(FloatingText(self.x, self.y - 150, name, self.b, S))
        d = damage * self.dmg_mult * self.ability_mult
        sfx("cast", 0.6)

        if k == "melee":
            if dist(self, t) < 140 * self.scale():
                self.deal(t, damage, True)
                t.knock(self.facing, 20)
                self.attack_landed = True
                burst(self.x + self.facing * 80, self.y - 95, self.b, 18, 190, 5)
                add_shake(5)
        elif k == "shot":
            shots.append(Shot(self, t, d, self.b, speed=700 * self.proj))
            sfx("shot", 0.6)
        elif k == "triple":
            for j in range(3):
                shots.append(Shot(self, t, d / 1.8, self.b,
                                  speed=(640 + j * 60) * self.proj, homing=1.4))
            sfx("shot", 0.7)
        elif k == "dash":
            self.dash(t, d, 190)
        elif k == "spin":
            if dist(self, t) < 170 * self.scale():
                self.deal(t, damage, True)
                t.knock(1 if t.x > self.x else -1, 24)
            for a in range(18):
                ang = a / 18 * math.tau
                particles.append([self.x + math.cos(ang) * 60, self.y - 70 + math.sin(ang) * 22,
                                  math.cos(ang) * 140, math.sin(ang) * 60, 0.4, 0.4, self.b, 5, 0])
            add_shake(4)
        elif k == "area":
            zones.append(Zone(t.x, t.y, 120, self.b, 2.6, d, t))
        elif k == "shield":
            self.shield = 4.5
            burst(self.x, self.y - 80, self.b, 20, 120, 5, grav=-150)
        elif k == "stun":
            if dist(self, t) < 330:
                shots.append(Shot(self, t, d, CYAN, speed=1100 * self.proj, homing=6, size=5))
                t.stun = 1.0
                floaters.append(FloatingText(t.x, t.y - 150, "STUNNED", CYAN, S))
        elif k in ("root", "arrest"):
            if dist(self, t) < 210:
                self.deal(t, damage, True)
                t.root = 1.7
                floaters.append(FloatingText(t.x, t.y - 150, "ROOTED", GOLD, S))
                for a in range(14):
                    ang = a / 14 * math.tau
                    particles.append([t.x + math.cos(ang) * 42, t.y + math.sin(ang) * 16,
                                      0, 0, 1.5, 1.5, GOLD, 4, 0])
        elif k == "heal":
            self.heal(damage)
        elif k == "hzone":
            zones.append(Zone(self.x, self.y, 110, GREEN, 3.0, damage, self, True, warn=0.0))
        elif k == "regen":
            self.buff = 5.0
            self.heal(damage)
        elif k == "life":
            if dist(self, t) < 160 * self.scale():
                got = self.deal(t, damage, True)
                self.heal(got * 0.6)
                for a in range(12):
                    particles.append([t.x, t.y - 90, (self.x - t.x) * 1.6, -60, 0.6, 0.6,
                                      self.b, 5, -40])
        elif k == "random":
            r = random.randint(1, 6)
            floaters.append(FloatingText(self.x, self.y - 175, f"ROLL {r}", GOLD, M))
            if r >= 3:
                self.deal(t, damage * r / 3.0, True)
                t.knock(self.facing, 12)
            else:
                self.hit(12)
                floaters.append(FloatingText(self.x, self.y - 150, "BUST", RED, S))
        elif k == "buff":
            self.buff = 4.5
            floaters.append(FloatingText(self.x, self.y - 150, "EMPOWERED", GOLD, S))
            burst(self.x, self.y - 80, GOLD, 18, 110, 4, grav=-140)

    def dash(self, t, damage, length):
        ox, oy = self.x, self.y
        self.x = max(MARGIN_X, min(W - MARGIN_X, self.x + self.facing * length))
        for i in range(12):                                    # after-image trail
            f = i / 12
            particles.append([ox + (self.x - ox) * f, oy - 80 + random.uniform(-22, 22),
                              -self.facing * 60, random.uniform(-30, 30),
                              0.35, 0.35, self.b, 6, 40])
        if abs(t.x - self.x) < 130 and abs(t.y - self.y) < 60:
            t.hit(damage)
            t.knock(self.facing, 22)
            add_shake(6)
            sfx("kick", 0.7)

    # ---- ultimate -------------------------------------------------------
    def special(self, t):
        if self.ult < 100 or not self.alive:
            return
        self.ult = 0.0
        name, damage = self.ultinfo
        d = damage * self.dmg_mult * self.ability_mult
        self.atk = self.atk_dur = 0.55
        self.atype = "cast"
        say(name.upper(), 1.2)
        burst(self.x, self.y - 80, self.b, 50, 300, 7, grav=-60)
        add_shake(10)
        add_flash(30)
        sfx("ult")

        if name == "Full Restore":
            self.hp = float(self.maxhp)
            self.buff = 5
            floaters.append(FloatingText(self.x, self.y - 150, "FULL RESTORE", GREEN, M))
        elif name == "House Always Wins":
            self.deal(t, damage * random.uniform(0.6, 1.6), True)
            t.knock(self.facing, 26)
        elif name == "Rain of Arrows":
            for j in range(7):
                zones.append(Zone(t.x + random.uniform(-130, 130),
                                  t.y + random.uniform(-50, 50),
                                  62, self.b, 2.4, d, t, warn=0.3 + j * 0.14))
        elif name in ("SWAT Raid", "Divine Judgment"):
            self.deal(t, damage, True)
            t.stun = 2.0
            zones.append(Zone(t.x, t.y, 140, self.b, 1.6, d * 0.25, t, warn=0.35))
        elif name == "Demon Rage":
            self.deal(t, damage, True)
            self.heal(35)
            self.buff = 5
            t.knock(self.facing, 24)
        elif name == "Sonic Speed":
            for _ in range(3):
                self.dash(t, damage / 3.0, 150)
            self.buff = 5
        elif name == "Inferno":
            zones.append(Zone(t.x, t.y, 165, self.b, 4.0, d, t, warn=0.4))
        elif name == "Earth Titan":
            self.shield = 6.0
            zones.append(Zone(self.x, self.y, 260, self.b, 0.9, d * 0.3, t, warn=0.3))
            if dist(self, t) < 300:
                self.deal(t, damage, True)
                t.knock(self.facing, 30)

    # ---- per-frame ------------------------------------------------------
    def up(self, dt):
        self.anim += dt
        moved = math.hypot(self.x - self.px, self.y - self.py)
        self.moving = moved > 0.4
        self.px, self.py = self.x, self.y

        if self.kx:                                    # knockback slide with friction
            self.x += self.kx * dt * 12
            self.kx *= max(0.0, 1 - dt * 9)
            if abs(self.kx) < 0.4:
                self.kx = 0.0
            self.clamp()

        self.hitstop = max(0.0, self.hitstop - dt)
        self.flinch = max(0.0, self.flinch - dt)
        self.atk = max(0.0, self.atk - dt)
        self.stun = max(0.0, self.stun - dt)
        self.root = max(0.0, self.root - dt)
        self.shield = max(0.0, self.shield - dt)
        self.buff = max(0.0, self.buff - dt)
        self.combo_t = max(0.0, self.combo_t - dt)
        if self.combo_t <= 0:
            self.combo = 0
        self.ult = min(100.0, self.ult + dt * 1.3 * self.ult_rate)
        for i in range(4):
            self.cd[i] = max(0.0, self.cd[i] - dt)
        if self.buff > 0 and random.random() < dt * 18:
            particles.append([self.x + random.uniform(-24, 24), self.y - random.uniform(0, 130),
                              0, -70, 0.5, 0.5, GOLD, 4, -40])
        if self.atk > 0 and self.atype in ("punch", "kick") and not self.attack_landed:
            target = e if self.player else p
            if target:
                self.resolve_basic(target)

    # ---- rendering ------------------------------------------------------
    def draw(self):
        x, y, f = self.x, self.y, self.facing
        s = self.scale()
        body = shade(self.c, 1.0)
        dark = shade(self.c, 0.55)      # limbs on the far side of the body
        lite = shade(self.c, 1.55)      # limbs on the near side — reads as lit
        acc = self.b
        if self.flinch > 0:                              # hit flash
            body = shade(body, 1.0 + self.flinch * 3)
            dark = shade(dark, 1.0 + self.flinch * 3)
            lite = shade(lite, 1.0 + self.flinch * 3)

        phase = 0.0
        if self.atk > 0:
            phase = max(0.0, min(1.0, 1 - self.atk / max(self.atk_dur, 1e-3)))
        swing = math.sin(phase * math.pi) if self.atk > 0 else 0.0
        kicking = self.atk > 0 and self.atype == "kick"
        punching = self.atk > 0 and self.atype == "punch"
        casting = self.atk > 0 and self.atype == "cast"
        walking = self.moving and self.atk <= 0 and self.stun <= 0

        # ground contact shadow, softened and scaled with depth
        sh = pygame.Surface((int(96 * s), int(26 * s)), pygame.SRCALPHA)
        pygame.draw.ellipse(sh, (0, 0, 0, 120), sh.get_rect())
        screen.blit(sh, (x - 48 * s, y - 8 * s))

        if not self.alive:                               # knocked out: lie down
            limb((x - 46 * s, y - 12 * s), (x + 34 * s, y - 8 * s), int(16 * s), body)
            pygame.draw.circle(screen, SKIN, ipt((x + 50 * s, y - 14 * s)), int(15 * s))
            return

        gait = math.sin(self.anim * 12)
        bob = (abs(math.sin(self.anim * 12)) * 5 if walking else math.sin(self.anim * 2.4) * 2.5)
        stun_wobble = math.sin(self.anim * 26) * 4 if self.stun > 0 else 0

        # --- torso / head anchors
        lean = 0.0
        if punching:
            lean = f * 9 * swing
        elif kicking:
            lean = -f * 12 * swing
        elif casting:
            lean = -f * 4 * swing
        hip = (x, y - 74 * s)
        neck = (x + lean + stun_wobble, y - (122 * s + bob))
        head = (neck[0] + f * 4, neck[1] - 25 * s)
        hr = int(15 * s)

        def knee_of(a, b, push):
            return ((a[0] + b[0]) / 2 + f * push * s, (a[1] + b[1]) / 2)

        # --- legs
        lw = int(12 * s)
        if kicking:
            # planted support leg, deeply bent; the body sits into it
            plant = (x - f * 20 * s, y)
            k_s = (x - f * 4 * s, y - 34 * s)
            limb(hip, k_s, lw, dark); limb(k_s, plant, lw, dark)
            pygame.draw.circle(screen, acc, ipt(plant), int(7 * s))
            # roundhouse: chamber the knee, then snap the shin out
            chamber = min(1.0, phase / 0.34)
            ext = max(0.0, min(1.0, (phase - 0.26) / 0.34))
            back = max(0.0, (phase - 0.62) / 0.38)
            drive = max(0.0, ext - back)
            foot = (x + f * (30 + 104 * drive) * s, y - (56 + 34 * chamber - 14 * drive) * s)
            knee = (x + f * (26 + 34 * chamber) * s, y - (52 + 18 * chamber) * s)
            limb(hip, knee, lw, lite); limb(knee, foot, int(11 * s), lite)
            pygame.draw.circle(screen, acc, ipt(foot), int(9 * s))
            if drive > 0.45:
                glow(foot[0], foot[1], int(30 * s), acc)
        elif walking:
            for sign, col in ((-1, dark), (1, lite)):
                ph = gait * sign
                lift = max(0.0, ph) * 15 * s
                foot = (x + f * ph * 27 * s, y - lift)
                knee = knee_of(hip, foot, 8 + ph * 8)
                limb(hip, knee, lw, col); limb(knee, foot, lw, col)
                pygame.draw.circle(screen, acc, ipt(foot), int(6 * s))
        else:
            stance = 1.12 if punching else 1.0
            for sign, col in ((-1, dark), (1, lite)):
                foot = (x + f * sign * 20 * s * stance, y)
                knee = knee_of(hip, foot, 9 + (4 if sign > 0 else 0))
                limb(hip, knee, lw, col); limb(knee, foot, lw, col)
                pygame.draw.circle(screen, acc, ipt(foot), int(6 * s))

        # --- torso + belt
        limb(hip, neck, int(17 * s), body)
        pygame.draw.circle(screen, acc, ipt((hip[0], hip[1] - 2 * s)), int(8 * s))

        # --- arms
        sh_f = (neck[0] + f * 11 * s, neck[1] + 6 * s)
        sh_b = (neck[0] - f * 11 * s, neck[1] + 8 * s)
        aw = int(10 * s)

        def arm(shoulder, hand, push, col):
            elbow = ((shoulder[0] + hand[0]) / 2 + f * push * s,
                     (shoulder[1] + hand[1]) / 2 + 10 * s)
            limb(shoulder, elbow, aw, col); limb(elbow, hand, int(9 * s), col)
            pygame.draw.circle(screen, acc, ipt(hand), int(7 * s))
            return hand

        if punching:
            reach = (30 + 78 * swing) * s
            lead = (x + f * reach, neck[1] + 2 * s)
            guard = (neck[0] - f * 2 * s, neck[1] - 14 * s)
            arm(sh_b, guard, -6, dark)
            arm(sh_f, lead, 2, lite)
            if swing > 0.5:
                glow(lead[0], lead[1], int(18 * s), acc)
        elif casting:
            up = (x + f * (28 + 30 * swing) * s, neck[1] - (28 + 28 * swing) * s)
            low = (x + f * (20 + 16 * swing) * s, neck[1] + 6 * s)
            arm(sh_b, low, -3, dark)
            arm(sh_f, up, 5, lite)
            glow(up[0], up[1], int((16 + 20 * swing) * s), acc)
        elif kicking:
            arm(sh_b, (neck[0] - f * 30 * s, neck[1] - 4 * s), -9, dark)
            arm(sh_f, (neck[0] + f * 20 * s, neck[1] - 20 * s), 6, lite)
        else:
            g = math.sin(self.anim * 2.4) * 2 * s
            arm(sh_b, (neck[0] + f * 3 * s, neck[1] - 16 * s + g), -8, dark)
            arm(sh_f, (neck[0] + f * 21 * s, neck[1] - 8 * s - g), 7, lite)

        # --- head
        pygame.draw.circle(screen, SKIN, ipt(head), hr)
        pygame.draw.circle(screen, shade(SKIN, 0.62), ipt(head), hr, max(1, int(2 * s)))
        hair = pygame.Rect(0, 0, hr * 2, hr)                    # hair as a top cap
        hair.center = (head[0] - f * 1.5 * s, head[1] - hr * 0.46)
        pygame.draw.ellipse(screen, dark, hair)
        pygame.draw.circle(screen, BLACK, ipt((head[0] + f * 6 * s, head[1] + 2 * s)),
                           max(1, int(2.2 * s)))

        # --- spirit orb drifting above the head
        orb = (x + f * 2, y - (192 * s + bob + math.sin(self.anim * 2.2) * 4))
        glow(orb[0], orb[1], int(20 * s), acc)
        pygame.draw.circle(screen, acc, ipt(orb), int(8 * s))
        pygame.draw.circle(screen, WHITE, ipt((orb[0] - 2.5 * s, orb[1] - 2.5 * s)), int(3 * s))

        # --- status rings
        if self.shield > 0:
            r = int(64 * s)
            a = int(58 + 40 * math.sin(self.anim * 7))
            srf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(srf, (acc[0], acc[1], acc[2], a), (r, r), r, 3)
            pygame.draw.circle(srf, (acc[0], acc[1], acc[2], 14), (r, r), r - 4)
            screen.blit(srf, (x - r, y - 92 * s - r))
        if self.stun > 0:
            for j in range(3):
                a = self.anim * 7 + j * math.tau / 3
                pygame.draw.circle(screen, GOLD,
                                   ipt((head[0] + math.cos(a) * 26 * s,
                                        head[1] - 22 * s + math.sin(a) * 8 * s)), int(4 * s))
        if self.root > 0:
            pygame.draw.ellipse(screen, GOLD, (x - 34 * s, y - 12 * s, 68 * s, 24 * s), 3)

        # --- floating nameplate + mini health bar
        bw = 104
        bx, by = x - bw / 2, y - 224 * s
        pygame.draw.rect(screen, (8, 10, 15), (bx - 2, by - 2, bw + 4, 11), border_radius=3)
        frac = self.hp / self.maxhp
        pygame.draw.rect(screen, GREEN if frac > 0.4 else RED,
                         (bx, by, int(bw * frac), 7), border_radius=3)
        txt(self.n, (x, by - 14), SM, acc, True)


# --------------------------------------------------------------------------
# HUD & screens
# --------------------------------------------------------------------------
def bar(x, y, w, h, frac, col, bg=(38, 46, 58), radius=4):
    pygame.draw.rect(screen, bg, (x, y, w, h), border_radius=radius)
    if frac > 0:
        pygame.draw.rect(screen, col, (x, y, int(w * max(0, min(1, frac))), h), border_radius=radius)


def hud():
    for fighter, x, flip in ((p, 30, False), (e, W - 30 - 455, True)):
        frac = fighter.hp / fighter.maxhp
        pygame.draw.rect(screen, (6, 9, 14), (x - 4, 26, 463, 34), border_radius=6)
        bar(x, 32, 455, 22, frac, GREEN if frac > 0.4 else (GOLD if frac > 0.18 else RED), (30, 36, 46))
        pygame.draw.rect(screen, (90, 104, 122), (x, 32, 455, 22), 2, border_radius=4)
        bar(x, 57, 455, 5, fighter.ult / 100, GOLD, (26, 30, 38), 2)
        label = f"{fighter.n}   {int(fighter.hp)}/{int(fighter.maxhp)}"
        txt(label, (x + 455 - S.size(label)[0], 6) if flip else (x, 6), S, WHITE)
        txt(fighter.cls, (x + 455 - SM.size(fighter.cls)[0], 66) if flip else (x, 66), SM, DIM)

    tcol = RED if timer <= 10 else GOLD
    pygame.draw.rect(screen, (6, 9, 14), (W // 2 - 46, 24, 92, 44), border_radius=8)
    txt(f"{int(math.ceil(timer)):02d}", (W // 2, 46), M, tcol, True)

    if p.combo > 1 and p.combo_t > 0:
        txt(f"{p.combo} HIT COMBO", (W // 2, 92), M, GOLD, True)

    # bottom ability rail
    pygame.draw.rect(screen, (8, 11, 17), (0, 628, W, H - 628))
    pygame.draw.line(screen, (40, 52, 68), (0, 628), (W, 628), 2)
    txt("ABILITIES", (24, 642), S, DIM)
    CW = 162
    for i, (name, cd, damage, k) in enumerate(p.abilities):
        x = 120 + i * (CW + 10)
        ready = p.cd[i] <= 0
        col = p.b if ready else (78, 86, 98)
        pygame.draw.rect(screen, (24, 32, 43), (x, 638, CW, 66), border_radius=8)
        pygame.draw.rect(screen, col, (x, 638, CW, 66), 2, border_radius=8)
        pygame.draw.rect(screen, col if ready else (56, 62, 74),
                         (x + 7, 644, 22, 22), border_radius=4)
        txt(str(i + 1), (x + 18, 655), S, BLACK if ready else (150, 156, 166), True)
        txt(name, (x + 35, 646), S, WHITE if ready else (140, 146, 156))
        txt(k.upper(), (x + 35, 665), SM, DIM if ready else (95, 100, 110))
        status = "READY" if ready else f"{p.cd[i]:.1f}s"
        txt(status, (x + CW - 8 - SM.size(status)[0], 665), SM, GREEN if ready else GOLD)
        frac = 1.0 if ready else 1 - p.cd[i] / max(p.cdmax[i], 1e-3)
        bar(x + 8, 688, CW - 16, 8, frac, p.b if ready else GOLD, (44, 52, 62), 3)

    # special meter
    full = p.ult >= 100
    sx, sw = 826, W - 24 - 826
    txt("SPECIAL  [SPACE]", (sx, 640), S, GOLD if full else DIM)
    uname = p.ultinfo[0].upper()
    txt(uname, (sx + sw - S.size(uname)[0], 640), S, GOLD if full else (128, 138, 152))
    bar(sx, 662, sw, 24, p.ult / 100, GOLD if full else (168, 134, 52), (34, 38, 46), 6)
    if full:
        pygame.draw.rect(screen, WHITE, (sx, 662, sw, 24), 2, border_radius=6)
    txt("READY!" if full else f"{int(p.ult)}%", (sx + sw // 2, 674), S,
        BLACK if p.ult > 48 else WHITE, True)
    ctl = "WASD move    J / LMB punch    K / RMB kick    1-4 abilities"
    txt(ctl, (sx + sw // 2, 698), SM, DIM, True)


def draw_stickman(cx, cy, c, t, scale=1.0):
    """Menu mannequin — same construction as the in-game fighter."""
    s = scale
    b = math.sin(t * 2.2) * 3
    hip = (cx, cy + 28 * s)
    neck = (cx, cy - 30 * s + b)
    head = (cx, cy - 54 * s + b)
    for sign, col in ((-1, shade(c, 0.55)), (1, shade(c, 1.0))):
        foot = (cx + sign * 26 * s, cy + 88 * s)
        knee = ((hip[0] + foot[0]) / 2 + sign * 6 * s, (hip[1] + foot[1]) / 2)
        limb(hip, knee, int(13 * s), col); limb(knee, foot, int(13 * s), col)
        pygame.draw.circle(screen, c, ipt(foot), int(8 * s))
    limb(hip, neck, int(18 * s), shade(c, 1.0))
    for sign, col in ((-1, shade(c, 0.55)), (1, shade(c, 1.35))):
        hand = (cx + sign * 34 * s, cy - 34 * s + b + sign * 4)
        sh = (cx + sign * 9 * s, cy - 22 * s + b)
        elbow = ((sh[0] + hand[0]) / 2 + sign * 6 * s, (sh[1] + hand[1]) / 2 + 8 * s)
        limb(sh, elbow, int(11 * s), col); limb(elbow, hand, int(10 * s), col)
        pygame.draw.circle(screen, c, ipt(hand), int(8 * s))
    pygame.draw.circle(screen, SKIN, ipt(head), int(20 * s))
    pygame.draw.circle(screen, shade(SKIN, 0.6), ipt(head), int(20 * s), 2)
    cap = pygame.Rect(0, 0, int(40 * s), int(20 * s))
    cap.center = (head[0], head[1] - 9 * s)
    pygame.draw.ellipse(screen, shade(c, 0.55), cap)
    orb = (cx, cy - 104 * s + b * 1.5)
    glow(orb[0], orb[1], int(26 * s), shade(c, 0.7))
    pygame.draw.circle(screen, c, ipt(orb), int(11 * s))
    pygame.draw.circle(screen, WHITE, ipt((orb[0] - 3.5, orb[1] - 3.5)), int(4 * s))


def menu(t):
    screen.fill((6, 10, 17))
    for i in range(16):                                   # perspective floor rays
        pygame.draw.line(screen, (15, 23, 35), (W // 2, 150), (i * 90 - 90, H), 2)
    for i in range(7):
        yy = 150 + i * i * 14
        pygame.draw.line(screen, (14, 21, 32), (0, yy), (W, yy), 1)

    # --- title
    w1 = B.size("SPIRIT ")[0]
    x0 = W // 2 - (w1 + B.size("FIGHTERS")[0]) // 2
    txt("SPIRIT ", (x0, 22), B, WHITE)
    txt("FIGHTERS", (x0 + w1, 22), B, GOLD)
    txt("TACTICAL 2D STICK-FIGHTER ARENA", (W // 2, 100), S, (140, 170, 195), True)
    pygame.draw.line(screen, (34, 46, 62), (40, 124), (W - 40, 124), 2)

    n = names[pick]
    cls, c, acc, hp, spd, abil, ult = SP[n]

    # --- left: portrait
    pygame.draw.rect(screen, (11, 17, 27), (52, 146, 452, 440), border_radius=12)
    pygame.draw.rect(screen, (36, 48, 64), (52, 146, 452, 440), 2, border_radius=12)
    cone = pygame.Surface((448, 436), pygame.SRCALPHA)          # spotlight beam
    pygame.draw.polygon(cone, (acc[0], acc[1], acc[2], 13),
                        [(198, 0), (250, 0), (392, 436), (56, 436)])
    screen.blit(cone, (54, 148), special_flags=pygame.BLEND_RGBA_ADD)
    ped = pygame.Rect(0, 0, 250, 46)
    ped.center = (278, 530)
    pygame.draw.ellipse(screen, (18, 26, 38), ped)
    pygame.draw.ellipse(screen, shade(acc, 0.55), ped, 2)
    draw_stickman(278, 418, acc, t, 1.18)

    # --- right: dossier
    RX = 548
    txt(n, (RX, 150), B, acc)
    txt(cls, (RX, 214), F, WHITE)
    stats = [("HEALTH", hp / 360, hp), ("SPEED", spd / 340, spd), ("SPECIAL POWER", ult[1] / 100, ult[1])]
    for i, (label, frac, raw) in enumerate(stats):
        y = 258 + i * 34
        txt(label, (RX, y), SM, DIM)
        bar(RX + 130, y + 2, 460, 11, frac, acc, (26, 32, 42), 5)
        txt(str(int(raw)), (RX + 600, y - 1), S, WHITE)

    pygame.draw.line(screen, (34, 46, 62), (RX, 372), (W - 52, 372), 1)
    txt("ABILITIES", (RX, 384), SM, GOLD)
    for i, (aname, acd, adm, ak) in enumerate(abil):
        y = 408 + i * 30
        pygame.draw.circle(screen, acc, (RX + 8, y + 9), 4)
        txt(f"{i + 1}", (RX + 26, y), SM, DIM)
        txt(aname, (RX + 46, y - 2), S, WHITE)
        txt(ak.upper(), (RX + 240, y), SM, DIM)
        txt(f"{acd:.1f}s CD", (RX + 360, y), SM, (130, 142, 158))
        if adm:
            txt(f"{adm} DMG", (RX + 450, y), SM, (130, 142, 158))
    txt("SPECIAL", (RX, 536), SM, GOLD)
    txt(ult[0], (RX + 90, 532), F, GOLD)

    # --- roster strip
    for i, nm in enumerate(names):
        x = W // 2 + (i - (len(names) - 1) / 2) * 130
        sel = i == pick
        col = SP[nm][2]
        r = pygame.Rect(x - 60, 600, 120, 44)
        pygame.draw.rect(screen, (24, 33, 46) if sel else (15, 21, 31), r, border_radius=7)
        pygame.draw.rect(screen, col if sel else (44, 54, 68), r, 3 if sel else 1, border_radius=7)
        pygame.draw.circle(screen, col, (int(x - 44), 622), 8 if sel else 6)
        txt(nm, (x + 12, 614), S, WHITE if sel else (130, 142, 156), True)

    eq = len(equipped)
    txt(f"LOADOUT: {eq} item{'s' if eq != 1 else ''} equipped", (W // 2, 660), S,
        GREEN if eq else DIM, True)
    txt("A/D or ←/→ SELECT      ENTER FIGHT      S SHOP      ESC QUIT",
        (W // 2, 692), F, GOLD, True)


SHOP_COLS, SHOP_ROWS = 3, 4
SHOP_PER_PAGE = SHOP_COLS * SHOP_ROWS
CAT_COL = {"COMBAT": RED, "MOBILITY": CYAN, "RANGED": GREEN, "POWER": GOLD, "SPECIAL": (190, 120, 255)}


def shop(t):
    screen.fill((7, 11, 18))
    pygame.draw.rect(screen, (11, 17, 27), (0, 0, W, 128))
    pygame.draw.line(screen, (40, 52, 68), (0, 128), (W, 128), 2)
    txt("SPIRIT SHOP", (40, 26), B, GOLD)
    txt("DEVELOPMENT MODE — EVERY ITEM IS FREE" if CFG.game.dev_mode else "SPEND YOUR GEMS",
        (44, 90), S, GREEN)
    txt("GEMS  ∞", (W - 44 - F.size("GEMS  ∞")[0], 34), F, CYAN)

    page = shop_sel // SHOP_PER_PAGE
    pages = (len(SHOP) + SHOP_PER_PAGE - 1) // SHOP_PER_PAGE
    txt(f"PAGE {page + 1}/{pages}", (W - 44 - S.size(f'PAGE {page + 1}/{pages}')[0], 72), S, DIM)

    for idx in range(page * SHOP_PER_PAGE, min(len(SHOP), (page + 1) * SHOP_PER_PAGE)):
        j = idx - page * SHOP_PER_PAGE
        r, c = j // SHOP_COLS, j % SHOP_COLS
        x, y = 44, 152
        x += c * 320
        y += r * 96
        cat, name, desc, eff = SHOP[idx]
        on = idx in equipped
        sel = idx == shop_sel
        ccol = CAT_COL.get(cat, CYAN)

        pygame.draw.rect(screen, (26, 38, 30) if on else (20, 27, 39), (x, y, 296, 84), border_radius=9)
        border = WHITE if sel else (GREEN if on else (48, 60, 76))
        wdt = 3 if sel else 2
        pygame.draw.rect(screen, border, (x, y, 296, 84), wdt, border_radius=9)
        pygame.draw.rect(screen, ccol, (x, y + 10, 4, 64), border_radius=2)

        txt(cat, (x + 14, y + 8), SM, ccol)
        txt(name, (x + 14, y + 24), F, WHITE if not on else GREEN)
        txt(desc, (x + 14, y + 50), SM, (168, 182, 198))
        tag = "EQUIPPED" if on else ("FREE" if CFG.game.dev_mode else "BUY")
        tw = SM.size(tag)[0]
        pygame.draw.rect(screen, (34, 74, 48) if on else (44, 52, 66),
                         (x + 296 - tw - 22, y + 8, tw + 14, 18), border_radius=4)
        txt(tag, (x + 296 - tw - 15, y + 10), SM, GREEN if on else GOLD)
        if sel:
            pygame.draw.rect(screen, shade(ccol, 0.9), (x - 5, y - 5, 306, 94), 1, border_radius=11)

    # live loadout summary so the shop is never abstract
    b = loadout()
    pygame.draw.rect(screen, (11, 17, 27), (0, 548, W, H - 548))
    pygame.draw.line(screen, (40, 52, 68), (0, 548), (W, 548), 2)
    txt("ACTIVE LOADOUT", (44, 560), S, GOLD)
    rows = [
        ("Damage", f"x{b['dmg']:.2f}", b["dmg"] > 1),
        ("Ability dmg", f"x{b['ability_dmg']:.2f}", b["ability_dmg"] > 1),
        ("Max health", f"{b['hp']:+d}", b["hp"] != 0),
        ("Damage taken", f"x{b['armor']:.2f}", b["armor"] < 1),
        ("Lifesteal", f"{int(b['lifesteal'] * 100)}%", b["lifesteal"] > 0),
        ("Move speed", f"x{b['speed']:.2f}", b["speed"] > 1),
        ("Cooldowns", f"x{b['cdr']:.2f}", b["cdr"] < 1),
        ("Special rate", f"x{b['ult_rate']:.2f}", b["ult_rate"] > 1),
        ("Start special", f"{int(b['ult'])}%", b["ult"] > 0),
        ("Projectiles", f"x{b['proj']:.2f}", b["proj"] > 1),
    ]
    for i, (label, val, good) in enumerate(rows):
        x = 44 + (i % 5) * 242
        y = 584 + (i // 5) * 26
        txt(label, (x, y), S, DIM)
        txt(val, (x + 140, y), S, GREEN if good else (110, 120, 134))
    if equipped:
        txt(f"{len(equipped)} items equipped — applied to your fighter at match start",
            (44, 640), S, GREEN)
    else:
        txt("Nothing equipped yet — highlight an item and press ENTER", (44, 640), S, GOLD)
    txt("←/→/↑/↓ MOVE      ENTER EQUIP / UNEQUIP      X CLEAR ALL      ESC BACK",
        (W // 2, 684), F, GOLD, True)


def result_screen(t):
    arena(t)
    for fighter in sorted((p, e), key=lambda z: z.y):
        fighter.draw()
    ov = pygame.Surface((W, H), pygame.SRCALPHA)
    ov.fill((0, 0, 0, 185))
    screen.blit(ov, (0, 0))
    col = GREEN if result == "VICTORY!" else RED if result == "DEFEAT" else GOLD
    glow(W // 2, 252, 300, shade(col, 0.09))
    pygame.draw.line(screen, shade(col, 0.5), (W // 2 - 300, 300), (W // 2 + 300, 300), 2)
    txt(result, (W // 2, 250), B, col, True)
    txt(f"{p.n}   vs   {e.n}", (W // 2, 330), M, WHITE, True)
    txt(f"{int(p.hp)} HP left", (W // 2 - 170, 386), F, GREEN, True)
    txt(f"{int(e.hp)} HP left", (W // 2 + 170, 386), F, RED, True)
    txt("ENTER  PLAY AGAIN          S  SHOP          ESC  MAIN MENU",
        (W // 2, 470), F, GOLD, True)


# --------------------------------------------------------------------------
# match flow
# --------------------------------------------------------------------------
def apply_loadout(fighter):
    b = loadout()
    fighter.maxhp = max(60, int(fighter.maxhp + b["hp"]))
    fighter.hp = float(fighter.maxhp)
    fighter.dmg_mult = b["dmg"]
    fighter.ability_mult = b["ability_dmg"]
    fighter.armor = b["armor"]
    fighter.lifesteal = b["lifesteal"]
    fighter.speed *= b["speed"]
    fighter.shield = b["shield"]
    fighter.ult = min(100.0, fighter.ult + b["ult"])
    fighter.ult_rate = b["ult_rate"]
    fighter.cdr = b["cdr"]
    fighter.proj = b["proj"]
    fighter.cdmax = [a[1] * b["cdr"] for a in fighter.abilities]


def start():
    global p, e, state, timer, particles, shots, zones, floaters, result, intro, ko_timer
    p = Fighter(names[pick], 380, 505, 1)
    apply_loadout(p)
    e = Fighter(random.choice([n for n in names if n != p.n]), 900, 505, 0)
    state = "fight"
    timer = CFG.game.round_time
    intro = CFG.game.intro_time
    ko_timer = 0.0
    particles = []; shots = []; zones = []; floaters = []
    result = ""
    say("FIGHT!", 1.1)
    sfx("ui")


def ai(dt):
    """Distance-keeping AI: closes to its preferred band, throws abilities on a
    loose timer, and backs off while it has nothing off cooldown."""
    if not e.alive or not p.alive or e.stun > 0:
        return
    dx, dy = p.x - e.x, p.y - e.y
    q = math.hypot(dx, dy) or 1
    ranged = e.n in ("Archer", "Police", "Healer")
    desired = 240 if ranged else 105
    low = e.hp / e.maxhp < 0.3

    if low and random.random() < 0.5:
        desired += 120
    if q > desired + 30:
        e.move(dx, dy, dt)
    elif q < desired - 30:
        e.move(-dx, -dy * 0.6, dt)
    else:
        e.move(0, math.sin(pygame.time.get_ticks() / 700) * 40, dt)
    if abs(dx) > 2:
        e.facing = 1 if dx > 0 else -1

    if q < 140 and random.random() < dt * 1.5:
        e.attack(p, "punch" if random.random() < 0.62 else "kick")
    for i in range(4):
        if e.cd[i] <= 0 and random.random() < dt * 0.45:
            e.ability(i, p)
            break
    if e.ult >= 100 and random.random() < dt * 0.5:
        e.special(p)


def finish():
    global result, state
    if not p.alive and not e.alive:
        result = "DRAW"
    elif not e.alive:
        result = "VICTORY!"
    elif not p.alive:
        result = "DEFEAT"
    else:
        # time-out: judged on remaining health *fraction*, so a high-HP
        # spirit doesn't win a decision it didn't earn
        a, b = p.hp / p.maxhp, e.hp / e.maxhp
        result = "VICTORY!" if a > b + 1e-6 else "DEFEAT" if b > a + 1e-6 else "DRAW"
    state = "result"


def update_world(dt):
    for a in particles[:]:
        a[0] += a[2] * dt
        a[1] += a[3] * dt
        a[3] += a[8] * dt
        a[2] *= max(0.0, 1 - dt * 1.4)
        a[4] -= dt
        if a[4] <= 0:
            particles.remove(a)
    for a in shots[:]:
        a.up(dt)
        if a.life <= 0:
            shots.remove(a)
    for z in zones[:]:
        z.up(dt)
        if z.life <= 0 and z.warn <= 0:
            zones.remove(z)
    for a in floaters[:]:
        a.up(dt)
        if a.life <= 0:
            floaters.remove(a)


def draw_world(t):
    arena(t)
    for z in zones:
        z.draw()
    for a in particles:
        if a[4] > 0:
            k = a[4] / a[5]
            r = max(1, int(a[7] * k))
            pygame.draw.circle(screen, shade(a[6], 0.4 + 0.6 * k), ipt((a[0], a[1])), r)
    for fighter in sorted((p, e), key=lambda z: z.y):   # depth ordering
        fighter.draw()
    for a in shots:
        a.draw()
    for a in floaters:
        a.draw()


# --------------------------------------------------------------------------
# main frame
# --------------------------------------------------------------------------
def frame(events, dt, t):
    global state, pick, shop_sel, timer, intro, ko_timer, shake, flash, banner_t

    running = True
    for ev in events:
        if ev.type == pygame.QUIT:
            running = False
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_F3:
            CFG.debug.overlay = not CFG.debug.overlay      # works in every state
        elif ev.type == pygame.KEYDOWN:
            if state == "menu":
                if ev.key in (pygame.K_RIGHT, pygame.K_d):
                    pick = (pick + 1) % len(names); sfx("ui")
                elif ev.key in (pygame.K_LEFT, pygame.K_a):
                    pick = (pick - 1) % len(names); sfx("ui")
                elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    start()
                elif ev.key == pygame.K_s:
                    state = "shop"; sfx("ui")
                elif ev.key == pygame.K_ESCAPE:
                    running = False
            elif state == "shop":
                if ev.key in (pygame.K_RIGHT, pygame.K_d):
                    shop_sel = (shop_sel + 1) % len(SHOP); sfx("ui")
                elif ev.key in (pygame.K_LEFT, pygame.K_a):
                    shop_sel = (shop_sel - 1) % len(SHOP); sfx("ui")
                elif ev.key in (pygame.K_DOWN, pygame.K_s):
                    shop_sel = (shop_sel + SHOP_COLS) % len(SHOP); sfx("ui")
                elif ev.key in (pygame.K_UP, pygame.K_w):
                    shop_sel = (shop_sel - SHOP_COLS) % len(SHOP); sfx("ui")
                elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if shop_sel in equipped:
                        equipped.discard(shop_sel)
                    else:
                        equipped.add(shop_sel)
                    write_save(); sfx("equip")
                elif ev.key == pygame.K_x:
                    equipped.clear(); write_save(); sfx("ui")
                elif ev.key == pygame.K_ESCAPE:
                    state = "menu"; sfx("ui")
            elif state == "fight":
                if ev.key == pygame.K_ESCAPE:
                    state = "menu"
                elif ev.key in (pygame.K_1, pygame.K_KP1):
                    p.ability(0, e)
                elif ev.key in (pygame.K_2, pygame.K_KP2):
                    p.ability(1, e)
                elif ev.key in (pygame.K_3, pygame.K_KP3):
                    p.ability(2, e)
                elif ev.key in (pygame.K_4, pygame.K_KP4):
                    p.ability(3, e)
                elif ev.key == pygame.K_j:
                    p.attack(e, "punch")
                elif ev.key in (pygame.K_k, pygame.K_LSHIFT, pygame.K_RSHIFT):
                    p.attack(e, "kick")
                elif ev.key == pygame.K_SPACE:
                    p.special(e)
            elif state == "result":
                if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    start()
                elif ev.key == pygame.K_s:
                    state = "shop"
                elif ev.key == pygame.K_ESCAPE:
                    state = "menu"
        elif ev.type == pygame.MOUSEBUTTONDOWN and state == "fight":
            if ev.button == 1:
                p.attack(e, "punch")
            elif ev.button == 3:
                p.attack(e, "kick")

    banner_t = max(0.0, banner_t - dt)
    shake = max(0.0, shake - dt * 60)
    flash = max(0.0, flash - dt * 320)

    if state == "fight":
        intro = max(0.0, intro - dt)
        k = pygame.key.get_pressed()
        scale = CFG.game.hitstop_timescale if (p.hitstop > 0 or e.hitstop > 0) else 1.0
        sdt = dt * scale
        p.move(k[pygame.K_d] - k[pygame.K_a], k[pygame.K_s] - k[pygame.K_w], sdt)
        p.up(sdt); e.up(sdt)
        if intro <= 0:
            ai(sdt)
            timer = max(0.0, timer - dt)
        update_world(sdt)
        if not p.alive or not e.alive or timer <= 0:
            state = "ko"
            ko_timer = CFG.game.ko_time
            say("K.O." if (not p.alive or not e.alive) else "TIME UP", 1.5)
            add_shake(10)
    elif state == "ko":
        sdt = dt * CFG.game.ko_timescale              # brief slow-motion finish
        p.up(sdt); e.up(sdt)
        update_world(sdt)
        ko_timer -= dt
        if ko_timer <= 0:
            finish()

    # ---- draw
    if state == "menu":
        menu(t)
    elif state == "shop":
        shop(t)
    elif state in ("fight", "ko"):
        draw_world(t)
        hud()
        if state == "ko":
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 90))
            screen.blit(ov, (0, 0))
    else:
        result_screen(t)

    if banner_t > 0 and state in ("fight", "ko"):
        a = min(1.0, banner_t / 0.4)
        sc = 1.0 + (1 - min(1.0, banner_t)) * 0.0
        col = shade(GOLD, 0.4 + 0.6 * a)
        txt(banner, (W // 2, 300), XL if state == "ko" else B, col, True)

    if flash > 0:
        ov = pygame.Surface((W, H), pygame.SRCALPHA)
        ov.fill((255, 90, 90, int(min(110, flash))))
        screen.blit(ov, (0, 0))
    return running


frame_ms = deque(maxlen=CFG.debug.samples)


def debug_overlay():
    """F3 readout. Every phase reports frame cost, so the cost stays visible."""
    if not frame_ms:
        return
    avg = sum(frame_ms) / len(frame_ms)
    worst = max(frame_ms)
    rows = [
        (f"{avg:5.2f} ms  avg     ({1000 / max(avg, 1e-6):.0f} fps)",
         GREEN if avg < 16.67 else RED),
        (f"{worst:5.2f} ms  worst of last {len(frame_ms)}",
         GREEN if worst < 16.67 else GOLD),
        (f"budget 16.67 ms   headroom {16.67 - avg:5.2f} ms", DIM),
        (f"particles {len(particles):<4} shots {len(shots):<3} "
         f"zones {len(zones):<3} floaters {len(floaters)}", DIM),
        (f"glow cache {len(_glow_cache)}/{CFG.glow.cache_max}   state {state}", DIM),
    ]
    pad = pygame.Surface((330, 18 * len(rows) + 14), pygame.SRCALPHA)
    pad.fill((0, 0, 0, 165))
    screen.blit(pad, (10, 96))
    for i, (line, col) in enumerate(rows):
        txt(line, (20, 104 + i * 18), SM, col)


def main():
    running = True
    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        t = pygame.time.get_ticks() / 1000.0
        t0 = time.perf_counter()

        running = frame(pygame.event.get(), dt, t)
        if CFG.debug.overlay:
            debug_overlay()

        display.fill(BLACK)
        if shake > 0.4:
            display.blit(screen, (random.uniform(-shake, shake), random.uniform(-shake, shake)))
        else:
            display.blit(screen, (0, 0))
        pygame.display.flip()
        frame_ms.append((time.perf_counter() - t0) * 1000.0)
    pygame.quit()


# --------------------------------------------------------------------------
# headless self-test — exercises every spirit, ability, ultimate and screen
# --------------------------------------------------------------------------
def selftest():
    global state, pick, shop_sel, p, e
    print("• fonts, arena and vignette built")

    for n in names:                                   # every ability of every spirit
        for m in names:
            if m == n:
                continue
        a = Fighter(n, 400, 500, 1)
        b = Fighter(random.choice([x for x in names if x != n]), 900, 500, 0)
        globals()["p"], globals()["e"] = a, b
        apply_loadout(a)
        for i in range(4):
            a.cd[i] = 0
            a.atk = 0
            a.ability(i, b)
            a.up(1 / 60)
            b.up(1 / 60)
            update_world(1 / 60)
        a.ult = 100
        a.special(b)
        b.ult = 100
        b.special(a)
        for kind in ("punch", "kick"):
            a.atk = 0
            a.attack(b, kind)
            for _ in range(30):
                a.up(1 / 60); b.up(1 / 60); update_world(1 / 60)
        for _ in range(40):
            a.up(1 / 60); b.up(1 / 60); update_world(1 / 60)
            draw_world(1.0)
        a.hp = 0; a.alive = False
        draw_world(1.0)
    print(f"• all {len(names)} spirits: 4 abilities + ultimate + punch + kick OK")

    def key(k):
        return pygame.event.Event(pygame.KEYDOWN, {"key": k, "unicode": "", "mod": 0})

    state = "menu"
    for _ in range(len(names) + 2):
        frame([key(pygame.K_d)], 1 / 60, 1.0)
    frame([key(pygame.K_s)], 1 / 60, 1.0)
    assert state == "shop", state
    for k in (pygame.K_RIGHT, pygame.K_DOWN, pygame.K_LEFT, pygame.K_UP):
        frame([key(k)], 1 / 60, 1.0)
    for i in (0, 5, 9, 14):
        shop_sel = i
        frame([key(pygame.K_RETURN)], 1 / 60, 1.0)
    assert len(equipped) == 4, equipped
    b = loadout()
    assert b["dmg"] > 1.0 and b["hp"] != 0, b
    frame([key(pygame.K_ESCAPE)], 1 / 60, 1.0)
    assert state == "menu"
    print(f"• shop: navigation, equip/unequip, persistence, loadout {b['dmg']:.2f}x dmg OK")

    frame([key(pygame.K_RETURN)], 1 / 60, 1.0)
    assert state == "fight"
    assert p.dmg_mult > 1.0 and p.maxhp != SP[p.n][3], "loadout not applied"
    print(f"• match start: {p.n} vs {e.n}, loadout applied ({p.maxhp} hp, x{p.dmg_mult:.2f} dmg)")

    fired = 0
    for i in range(2600):
        evs = []
        if i % 17 == 0:
            evs.append(key(random.choice([pygame.K_j, pygame.K_k])))
        if i % 29 == 0:
            evs.append(key(random.choice([pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4])))
            fired += 1
        if i % 61 == 0:
            evs.append(key(pygame.K_SPACE))
        if state == "result":
            break
        frame(evs, 1 / 60, i / 60)
    assert state == "result", f"match never resolved (state={state})"
    print(f"• fight → ko → result reached in {i} frames, result={result!r}")

    frame([key(pygame.K_RETURN)], 1 / 60, 1.0)
    assert state == "fight"
    frame([key(pygame.K_ESCAPE)], 1 / 60, 1.0)
    assert state == "menu"
    for _ in range(20):
        frame([], 1 / 60, 1.0)
    print("• rematch + return to menu OK")

    equipped.clear()
    write_save()
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    if SELFTEST:
        selftest()
        pygame.quit()
    else:
        main()
