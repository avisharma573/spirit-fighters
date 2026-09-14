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

# The world is rendered larger than the window so the camera has somewhere to
# move; the camera blits a sub-rect of it down to the 1280x720 display. Arena
# geometry is authored in 1280x720 design units and scaled by KS.
WW, WH = 1600, 900
KS = WW / 1280.0


def ks(v):
    """Design-space (1280x720) value -> world-space value."""
    return v * KS

display = pygame.display.set_mode((W, H))
pygame.display.set_caption("Spirit Fighters — Neon Citadel Stadium")
# Everything is drawn to this offscreen buffer so the whole frame can be
# shaken on impact before it reaches the window.
UI = pygame.Surface((W, H)).convert()            # 1280x720 — HUD, menus, overlays
WORLD = pygame.Surface((WW, WH)).convert()       # 1600x900 — the arena and fighters
# `screen` is whichever of the two is currently being drawn into. Every drawing
# helper resolves it as a global at call time, so swapping it retargets them all.
screen = UI


def target(surf):
    global screen
    screen = surf
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
        ground_top=456,         # nearest / furthest the feet may stand
        ground_bottom=604,
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
    motion=Cfg(                 # PHASE B — skeleton, IK, procedural locomotion
        substep=1 / 120.0,      # springs integrate at a fixed rate, always
        max_substeps=6,
        thigh=47, shin=47,      # bone lengths in design units (scaled by depth)
        max_extend=0.80,        # force a step before the leg locks straight
        upper_arm=27, forearm=25,
        hip_h=74, chest_h=120, head_h=150,   # rest heights above the feet
        shoulder_w=11, stance_width=19,
        stride=52,              # distance between footfalls
        gap_trigger=0.32,       # step when hips outrun the planted foot by this
        plant_ahead=0.52,       # how far ahead of the hips the foot lands
        swing_time=0.17,        # seconds a foot spends in the air
        step_lift=17,           # how high the swing foot arcs
        replant_dist=115,       # beyond this the feet snap (dash, knockback)
        lean_k=150.0, lean_d=15.0, lean_per_speed=0.030, lean_max=10.0,
        head_k=190.0, head_d=16.0, head_look=0.10, head_max=9.0,
        chest_k=240.0, chest_d=17.0,
        breath_rate=2.3, breath_amp=2.6,
        weight_shift=2.2,       # idle hip sway amplitude
        hit_impulse=190.0,      # spring kick per unit of damage direction
        ragdoll_blend=0.15,     # seconds to go from animated to full physics
        ragdoll_gravity=2300.0,
        ragdoll_damping=0.985,
        limb_taper=0.62,        # extremity width as a fraction of the root width
    ),
    feel=Cfg(                   # PHASE C — response and weight
        sim_hz=120.0,           # fixed simulation rate; render interpolates off it
        max_sim_steps=5,
        accel=2600.0,           # ground acceleration toward the input direction
        friction=11.0,          # exponential decay when there is no input
        turn_boost=1.7,         # extra accel when reversing, so turns stay crisp
        attack_buffer=0.13,     # an attack pressed this early still comes out
        trauma_decay=1.9,       # trauma falls off per second; shake = trauma^2
        trauma_max=1.0,
        shake_pixels=34.0,      # world-space shake at full trauma
        shake_zoom=0.045,       # camera punches in slightly on impact
    ),
    camera=Cfg(                 # PHASE C — dynamic follow + zoom
        # all distances below are WORLD units (the world is 1600x900)
        view_min=1120.0,        # view width when the fighters are close in
        view_max=1580.0,        # ... and when they are far apart
        # solved so a 150-unit clinch frames at view_min and a 900-unit
        # split frames at view_max — the range fights actually occupy
        sep_gain=0.61,          # how strongly separation opens the framing
        margin=1030.0,          # baseline framing width
        deadzone_x=55.0,        # camera ignores movement inside this box
        deadzone_y=32.0,
        lookahead=0.16,         # lead the camera along the fighters' velocity
        follow=6.5,             # position spring rate
        zoom_follow=3.4,        # framing spring rate (slower = calmer)
        bias_y=-172.0,          # lift the framing so the stands stay in shot
    ),
    light=Cfg(                  # PHASE E — one directional source
        angle=-118.0,           # degrees; where the key light comes FROM
        color=(255, 244, 214),
        rim=0.55,               # strength of the edge light on the figures
        shadow_len=0.42,        # how far shadows are thrown along the light
        shadow_alpha=118,
    ),
    post=Cfg(                   # PHASE E — post chain, deliberately SUBTLE
        enabled=True,
        bloom=0.85,             # additive strength of the bloom pass
        bloom_soft=2,           # extra downsample steps; higher = wider glow
        grade_gain=(255, 249, 240),   # multiply (highlights)
        grade_lift=(5, 4, 9),         # add (shadows)
        vignette=True,
        grain=0.16,             # film grain opacity
        grain_tiles=6,
        chroma=0.0,             # edge chromatic aberration; 0 = off
    ),
    fx=Cfg(                     # PHASE F — particles and weather
        pool=1400,              # hard cap; particles are recycled, never allocated
        dust_per_step=7,        # puff kicked up by a footfall
        debris_per_hit=9,
        ambient=70,             # drifting motes, split across two depths
        ambient_rate=0.5,
        weather="rain",         # "rain" | "snow" | "none"
        weather_count=260,
        rain_speed=1250.0,
        rain_len=26.0,
        splash_chance=0.30,
    ),
    audio=Cfg(                  # PHASE G
        enabled=True,
        master=0.85,
        variants=5,             # pitch-shifted copies baked per sound
        pitch_spread=0.13,      # +/- fraction of playback rate
        ambient=0.34,           # crowd + wind bed
    ),
    debug=Cfg(
        overlay=False,          # F3 toggles the frame-time readout
        samples=90,             # rolling window for the ms/frame average
        skeleton=False,         # F4 draws the joints and foot plants
    ),
)

# --------------------------------------------------------------------------
# audio — synthesised at boot, silently skipped if numpy/audio is unavailable
# --------------------------------------------------------------------------
SND = {}


def _build_sounds():
    """Synthesise every sound at boot. Each gets several pitch variants so a
    repeated hit never sounds like the same sample twice."""
    import numpy as np
    if pygame.mixer.get_init() is None:
        pygame.mixer.init(44100, -16, 1, 512)
    rate = pygame.mixer.get_init()[0]

    def bake(samples, vol):
        a = np.clip(samples, -1.0, 1.0) * vol
        return (a * 32767).astype(np.int16)

    def env(n, attack=0.004, power=2.2):
        t = np.linspace(0.0, 1.0, n, False)
        a = np.clip(t / max(attack, 1e-5), 0, 1)
        return a * (1.0 - t) ** power

    def tone(freq_a, freq_b, dur, noise=0.0, power=2.2, vol=0.25):
        n = int(rate * dur)
        f = np.linspace(freq_a, freq_b, n)
        wave = np.sin(2 * np.pi * np.cumsum(f) / rate)
        if noise:
            wave = wave * (1 - noise) + np.random.uniform(-1, 1, n) * noise
        return bake(wave * env(n, power=power), vol)

    def variants(arr):
        """Resample to a few pitches; playing a random one kills the loop feel."""
        out = []
        nv = max(1, CFG.audio.variants)
        for i in range(nv):
            r = 1.0 + CFG.audio.pitch_spread * ((i / max(1, nv - 1)) * 2 - 1)
            n = max(8, int(len(arr) / r))
            idx = np.clip((np.arange(n) * r).astype(np.int32), 0, len(arr) - 1)
            out.append(pygame.sndarray.make_sound(np.ascontiguousarray(arr[idx])))
        return out

    spec = {
        "punch": tone(190, 70, 0.16, noise=0.55, vol=0.30),
        "kick": tone(150, 48, 0.24, noise=0.45, vol=0.34),
        "whoosh": tone(900, 260, 0.16, noise=0.80, power=1.4, vol=0.13),
        "hit": tone(420, 120, 0.13, noise=0.35, vol=0.22),
        "cast": tone(300, 880, 0.22, noise=0.05, power=1.6, vol=0.18),
        "shot": tone(760, 300, 0.14, noise=0.15, vol=0.16),
        "boom": tone(130, 40, 0.45, noise=0.70, power=1.6, vol=0.36),
        "ult": tone(180, 1100, 0.70, noise=0.10, power=1.1, vol=0.32),
        "heal": tone(520, 980, 0.30, noise=0.0, power=1.6, vol=0.16),
        "ko": tone(320, 60, 0.80, noise=0.25, power=1.2, vol=0.34),
        "ui": tone(700, 700, 0.05, noise=0.0, power=3.0, vol=0.12),
        "equip": tone(520, 1040, 0.14, noise=0.0, power=2.0, vol=0.16),
        "step": tone(240, 90, 0.09, noise=0.85, power=3.0, vol=0.16),
    }
    for name, arr in spec.items():
        SND[name] = variants(arr)

    # Ambient bed: filtered noise that swells, plus a low wind drone. Looped
    # quietly under everything so the stadium is never silent.
    n = int(rate * 6.0)
    noise = np.random.uniform(-1, 1, n)
    kernel = np.ones(700) / 700.0
    crowd = np.convolve(noise, kernel, mode="same") * 6.0
    swell = 0.55 + 0.45 * np.sin(np.linspace(0, math.tau * 2.5, n))
    wind = np.sin(2 * np.pi * np.cumsum(np.full(n, 42.0)) / rate) * 0.10
    bed = np.clip(crowd * swell + wind, -1, 1) * 0.5
    fade = int(rate * 0.5)
    ramp = np.ones(n)
    ramp[:fade] = np.linspace(0, 1, fade)
    ramp[-fade:] = np.linspace(1, 0, fade)
    SND["_bed"] = [pygame.sndarray.make_sound(
        np.ascontiguousarray((bed * ramp * 32767).astype(np.int16)))]


AUDIO_ERROR = None
try:
    _build_sounds()
except Exception as _exc:          # never fatal, but never silent either
    SND.clear()
    AUDIO_ERROR = repr(_exc)


def sfx(name, vol=1.0):
    """Play a random pitch variant at a slightly random volume."""
    if not CFG.audio.enabled:
        return
    group = SND.get(name)
    if not group:
        return
    try:
        snd = group[random.randrange(len(group))]
        snd.set_volume(max(0.0, min(1.0, vol * CFG.audio.master
                                    * random.uniform(0.82, 1.0))))
        snd.play()
    except Exception:
        pass


_bed_chan = [None]


def start_ambient():
    if not CFG.audio.enabled or "_bed" not in SND:
        return
    try:
        if _bed_chan[0] is None or not _bed_chan[0].get_busy():
            _bed_chan[0] = SND["_bed"][0].play(loops=-1)
        if _bed_chan[0]:
            _bed_chan[0].set_volume(CFG.audio.ambient * CFG.audio.master)
    except Exception:
        pass


def stop_ambient():
    try:
        if _bed_chan[0]:
            _bed_chan[0].fadeout(400)
            _bed_chan[0] = None
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
shots = []; zones = []; floaters = []
state = "menu"; pick = 0; p = e = None; result = ""
timer = CFG.game.round_time; intro = 0.0; ko_timer = 0.0
shop_sel = 0
shake = 0.0; flash = 0.0; sim_acc = 0.0
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
    """Cached radial glow, built for additive blending.

    BLEND_RGBA_ADD adds the source RGB and ignores its alpha, so an alpha ramp
    does nothing — every pixel inside the disc adds the full colour, which is
    why a glow built that way renders as a hard-edged blob. The falloff has to
    live in the RGB itself: each ring is the colour scaled by its intensity,
    at full alpha.

    The key is quantised because callers pass continuously-varying colours
    (a fading zone, a pulsing aura); an exact key leaked a surface per frame.
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
        r0, g0, b0 = key[1], key[2], key[3]
        for r in range(radius, 0, -1):
            f = (1 - r / radius) ** CFG.glow.falloff
            c = (int(r0 * f), int(g0 * f), int(b0 * f), 255)
            if c[0] or c[1] or c[2]:
                pygame.draw.circle(g, c, (radius, radius), r)
        _glow_cache[key] = g
    return g


def glow(x, y, radius, color, surf=None):
    g = glow_surf(radius, color)
    r = g.get_width() // 2          # the cache may have rounded the radius
    (surf or screen).blit(g, (x - r, y - r), special_flags=pygame.BLEND_RGBA_ADD)
    # Anything glowing in the world is by definition emissive, so it also goes
    # into the bloom buffer. Explicit `surf=` callers are baking, not emitting.
    if surf is None and screen is WORLD and CFG.post.enabled:
        EMIS.blit(g, (x - r, y - r), special_flags=pygame.BLEND_RGBA_ADD)


def limb(a, b, w, col, surf=None):
    """A rounded capsule — the trick that makes stick limbs read as bodies."""
    surf = surf or screen
    pygame.draw.line(surf, col, a, b, w)
    pygame.draw.circle(surf, col, ipt(a), w // 2)
    pygame.draw.circle(surf, col, ipt(b), w // 2)


def taper(surf, a, b, w1, w2, col):
    """A capsule that narrows from w1 to w2 — thicker at the torso, thinner at
    the extremities. Drawn into the supersampled buffer, so its edges come out
    anti-aliased after the downscale."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1e-5
    nx, ny = -dy / d * 0.5, dx / d * 0.5
    pygame.draw.polygon(surf, col, [
        (a[0] + nx * w1, a[1] + ny * w1), (b[0] + nx * w2, b[1] + ny * w2),
        (b[0] - nx * w2, b[1] - ny * w2), (a[0] - nx * w1, a[1] - ny * w1)])
    pygame.draw.circle(surf, col, (int(a[0]), int(a[1])), int(w1 * 0.5))
    pygame.draw.circle(surf, col, (int(b[0]), int(b[1])), int(w2 * 0.5))


# Figures are rendered at SS× into this scratch buffer and scaled down, which
# anti-aliases the entire silhouette in one pass. Allocated once, never resized.
SS = 2
FIG_W, FIG_H = 460, 380
_fig_buf = pygame.Surface((FIG_W * SS, FIG_H * SS), pygame.SRCALPHA)


def shade(col, f):
    return (max(0, min(255, int(col[0] * f))),
            max(0, min(255, int(col[1] * f))),
            max(0, min(255, int(col[2] * f))))


# --------------------------------------------------------------------------
# PHASE F — pooled particles
#
# Every particle lives in a fixed-size ring of preallocated slots. The old list
# allocated a fresh list per particle and removed by value (O(n) per removal,
# O(n^2) per frame); this allocates nothing per frame and compacts in one pass.
# Slot layout: x y vx vy life maxlife r g b size grav drag kind
# --------------------------------------------------------------------------
P_X, P_Y, P_VX, P_VY, P_LIFE, P_MAX, P_R, P_G, P_B, P_SZ, P_GRAV, P_DRAG, P_KIND = range(13)
K_SPARK, K_SMOKE, K_DUST, K_RAIN, K_MOTE = range(5)

particles = [[0.0] * 13 for _ in range(CFG.fx.pool)]
_p_live = [0]                     # particles occupy slots [0, _p_live)


def spawn(x, y, vx, vy, life, col, size, grav=520.0, drag=1.4, kind=K_SPARK):
    n = _p_live[0]
    if n >= CFG.fx.pool:
        return None
    a = particles[n]
    _p_live[0] = n + 1
    a[P_X] = x; a[P_Y] = y; a[P_VX] = vx; a[P_VY] = vy
    a[P_LIFE] = life; a[P_MAX] = life
    a[P_R], a[P_G], a[P_B] = col[0], col[1], col[2]
    a[P_SZ] = size; a[P_GRAV] = grav; a[P_DRAG] = drag; a[P_KIND] = kind
    return a


def burst(x, y, c, n=15, power=180, size=5, grav=520, life=0.55):
    for _ in range(n):
        ang = random.random() * math.tau
        v = random.uniform(power * 0.25, power)
        spawn(x, y, math.cos(ang) * v, math.sin(ang) * v,
              life * (0.5 + random.random() * 0.8), c,
              size * (0.55 + random.random() * 0.7), grav)


def smoke(x, y, c, n=8, power=60):
    for _ in range(n):
        ang = random.random() * math.tau
        v = random.uniform(10, power)
        spawn(x, y, math.cos(ang) * v, math.sin(ang) * v - 30,
              0.7 + random.random() * 0.7, c, 7 + random.random() * 6,
              -40, 1.1, K_SMOKE)


def dust(x, y, scale=1.0):
    """Kicked up by a footfall. Surface-tinted, low and slow."""
    for _ in range(CFG.fx.dust_per_step):
        ang = -math.pi * 0.5 + random.uniform(-1.1, 1.1)
        v = random.uniform(18, 62) * scale
        spawn(x + random.uniform(-6, 6), y, math.cos(ang) * v, math.sin(ang) * v * 0.5,
              0.34 + random.random() * 0.30, (92, 104, 86),
              (4 + random.random() * 5) * scale, -22.0, 2.4, K_DUST)


def debris(x, y, c, direction=0.0):
    for _ in range(CFG.fx.debris_per_hit):
        ang = random.random() * math.tau
        v = random.uniform(90, 300)
        spawn(x, y, math.cos(ang) * v + direction * 120, math.sin(ang) * v - 40,
              0.30 + random.random() * 0.45, c, 2 + random.random() * 3, 900.0)


def add_shake(v):
    """Impacts add *trauma*, not shake. Shake is trauma squared, so small hits
    barely register and big ones hit hard — and it always decays to nothing."""
    global shake
    shake = min(CFG.feel.trauma_max, shake + v * 0.055)


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
HORIZON = 470                   # world y where the stands end and turf begins
PITCH = pygame.Rect(int(ks(20)), HORIZON, int(ks(1280 - 40)), 400)
# derived from CFG so the hot paths below read cleanly; edit CFG.arena, not these
GROUND_TOP = ks(CFG.arena.ground_top)
GROUND_BOTTOM = ks(CFG.arena.ground_bottom)
MARGIN_X = ks(CFG.arena.margin_x)


# --------------------------------------------------------------------------
# PHASE D — layered stadium with parallax and atmospheric perspective
#
# The background used to be one flat bake. It is now six layers at different
# depths. Distant layers are desaturated and washed toward the sky colour, are
# softened, and move less as the camera pans; the nearest layer passes in FRONT
# of the fighters. That separation is what reads as real depth.
# --------------------------------------------------------------------------
PAD = 300                       # slack each side so panning never shows an edge
LW = WW + PAD * 2
SKYTOP = (6, 9, 18)
SKYHORIZON = (21, 30, 49)
HAZE = (30, 44, 66)             # colour distant things wash out toward


def tint(surf, col, amount):
    """Atmospheric perspective: wash a layer toward the haze colour."""
    if amount <= 0:
        return surf
    ov = pygame.Surface(surf.get_size(), pygame.SRCALPHA)
    ov.fill((col[0], col[1], col[2], int(255 * amount)))
    surf.blit(ov, (0, 0))
    return surf


def soften(surf, amount):
    """Cheap depth-of-field: round-trip through a smaller surface."""
    if amount <= 1:
        return surf
    w, h = surf.get_size()
    small = pygame.transform.smoothscale(surf, (max(1, w // amount), max(1, h // amount)))
    return pygame.transform.smoothscale(small, (w, h))


def _layer(h, alpha=True):
    return pygame.Surface((LW, h), pygame.SRCALPHA if alpha else 0)


def build_sky():
    sky = pygame.Surface((LW, 500)).convert()
    h = sky.get_height()
    for y in range(h):
        u = y / h
        sky.fill((int(SKYTOP[0] + (SKYHORIZON[0] - SKYTOP[0]) * u),
                  int(SKYTOP[1] + (SKYHORIZON[1] - SKYTOP[1]) * u),
                  int(SKYTOP[2] + (SKYHORIZON[2] - SKYTOP[2]) * u)), (0, y, LW, 1))
    rng = random.Random(7)
    for _ in range(260):                                   # stars
        sx, sy = rng.randint(0, LW), rng.randint(0, int(h * 0.72))
        v = rng.randint(45, 150)
        r = 1 if v < 120 else 2
        pygame.draw.circle(sky, (v, v, min(255, v + 25)), (sx, sy), r)
    mx, my = int(LW * 0.76), int(ks(66))                   # moon, with a halo
    glow(mx, my, int(ks(120)), (52, 58, 78), surf=sky)
    pygame.draw.circle(sky, (214, 218, 232), (mx, my), int(ks(26)))
    pygame.draw.circle(sky, (188, 194, 212), (mx - int(ks(7)), my - int(ks(5))), int(ks(7)))
    pygame.draw.circle(sky, (196, 201, 218), (mx + int(ks(9)), my + int(ks(8))), int(ks(5)))
    # thin cloud bands catching the moonlight
    for i in range(7):
        cy = rng.randint(int(ks(30)), int(ks(210)))
        cw = rng.randint(int(ks(200)), int(ks(560)))
        cx = rng.randint(0, LW)
        band = pygame.Surface((cw, int(ks(26))), pygame.SRCALPHA)
        pygame.draw.ellipse(band, (58, 66, 88, 46), band.get_rect())
        sky.blit(soften(band, 3), (cx, cy))
    return sky


def _crowd(surf, y0, rows, seat_col, density, rng, bright=0.02):
    pygame.draw.rect(surf, seat_col, (0, y0 - ks(4), LW, rows * ks(13) + ks(8)))
    sz = max(2, int(ks(5)))
    for r in range(rows):
        yy = y0 + r * ks(13)
        for x in range(0, LW, int(ks(8))):
            if rng.random() > density:
                continue
            v = max(8, rng.randint(26, 54) - r * 4)
            c = (v, v + 3, v + 11)
            if rng.random() < bright:
                c = rng.choice([(104, 44, 44), (44, 74, 116), (118, 100, 46), (46, 104, 76)])
            pygame.draw.rect(surf, c, (x, yy + rng.randint(-2, 1), sz, sz))


def build_far():
    """Upper deck across the back of the bowl — hazy and out of focus."""
    lay = _layer(260)
    rng = random.Random(11)
    _crowd(lay, ks(60), 5, (17, 22, 33), 0.80, rng, 0.012)
    for x in range(0, LW, int(ks(150))):                   # structural ribs
        pygame.draw.line(lay, (26, 33, 46), (x, ks(30)), (x, ks(150)), max(1, int(ks(3))))
    lay = soften(lay, 3)
    return tint(lay, HAZE, 0.55)


def build_mid():
    """Main tier plus the roof trusses."""
    lay = _layer(300)
    rng = random.Random(23)
    for x in range(int(-ks(140)), LW + int(ks(160)), int(ks(105))):
        pygame.draw.line(lay, (38, 48, 64), (x, ks(10)), (x + ks(120), ks(120)), max(2, int(ks(3))))
    pygame.draw.rect(lay, (19, 25, 37), (0, ks(120), LW, ks(40)))
    _crowd(lay, ks(150), 4, (22, 28, 40), 0.86, rng, 0.022)
    lay = soften(lay, 2)
    return tint(lay, HAZE, 0.28)


def build_near():
    """Lower bowl: tunnels, floodlight towers, perimeter hoardings."""
    lay = _layer(360)
    rng = random.Random(31)
    _crowd(lay, ks(10), 3, (26, 33, 47), 0.9, rng, 0.03)
    pygame.draw.rect(lay, (11, 15, 23), (0, ks(78), LW, ks(18)))      # shadow line
    for tx in (PAD + ks(36), PAD + WW - ks(116)):                     # tunnels
        mouth = pygame.Rect(int(tx), int(ks(96)), int(ks(80)), int(ks(150)))
        rad = int(ks(38))
        pygame.draw.rect(lay, (7, 9, 14), mouth, border_radius=rad)
        for i in range(5):
            inner = mouth.inflate(-ks(10 + i * 9), -ks(10 + i * 9))
            inner.bottom = mouth.bottom - int(ks(4))
            v = 12 + i * 6
            pygame.draw.rect(lay, (v + 8, v + 4, v), inner, border_radius=int(ks(26)))
        pygame.draw.rect(lay, (46, 58, 74), mouth, int(ks(4)), border_radius=rad)
        pygame.draw.rect(lay, (76, 94, 118), (tx + ks(6), ks(90), ks(68), ks(8)), border_radius=3)
    for x in (PAD + ks(96), PAD + WW - ks(108)):                      # light towers
        pygame.draw.rect(lay, (36, 44, 56), (x, ks(0), ks(14), ks(190)), border_radius=4)
        pygame.draw.rect(lay, (46, 56, 70), (x - ks(14), ks(-8), ks(42), ks(22)), border_radius=5)
    return lay


def build_pitch():
    """The ground the fighters stand on. Parallax 1.0 — no offset, no padding."""
    g = pygame.Surface((WW, WH), pygame.SRCALPHA)
    g.fill((9, 13, 20, 255), pygame.Rect(0, HORIZON - 8, WW, WH - HORIZON + 8))
    for j in range(9):                                     # mown rings
        f = 1 - j * 0.105
        tone = 1.0 + (0.16 if j % 2 else 0.0)
        col = shade((17, 40, 33), tone + (8 - j) * 0.02)
        rect = pygame.Rect(0, 0, int(PITCH.w * f), int(PITCH.h * f))
        rect.center = (PITCH.centerx, PITCH.centery)
        pygame.draw.ellipse(g, col, rect)
    line = (74, 122, 140)
    pygame.draw.ellipse(g, line, PITCH, int(ks(4)))
    pygame.draw.ellipse(g, shade(line, 0.6), PITCH.inflate(-ks(150), -ks(110)), 2)
    pygame.draw.circle(g, line, (WW // 2, PITCH.centery), int(ks(118)), 2)
    pygame.draw.circle(g, line, (WW // 2, PITCH.centery), int(ks(40)), 2)
    pygame.draw.circle(g, shade(line, 0.8), (WW // 2, PITCH.centery), int(ks(5)))
    pygame.draw.line(g, line, (WW // 2, PITCH.top + ks(16)),
                     (WW // 2, PITCH.bottom - ks(16)), 2)
    # pools of floodlight on the turf, and a specular sheen down the middle
    for lx in (ks(260), WW - ks(260)):
        glow(lx, PITCH.centery, int(ks(300)), (16, 19, 15), surf=g)
    # Crop to the ground band and drop the alpha channel: everything below the
    # horizon is solid, and an opaque blit is far cheaper than a full-size
    # SRCALPHA one that is mostly empty.
    band = pygame.Rect(0, HORIZON - 8, WW, WH - HORIZON + 8)
    return g.subsurface(band).copy().convert()


def build_fore():
    """Barrier rail that passes in FRONT of the fighters."""
    lay = _layer(90)
    pygame.draw.rect(lay, (13, 18, 27), (0, ks(30), LW, ks(30)))
    for yy in (ks(16), ks(32)):
        pygame.draw.line(lay, (54, 66, 84), (0, yy), (LW, yy), max(2, int(ks(4))))
    for xx in range(0, LW, int(ks(30))):
        pygame.draw.line(lay, (40, 50, 66), (xx, ks(10)), (xx, ks(36)), max(2, int(ks(4))))
    return lay


class Layer:
    __slots__ = ("surf", "y", "px", "pad")

    def __init__(self, surf, y, px, pad=True):
        self.surf = surf; self.y = y; self.px = px; self.pad = pad


SKY = build_sky()
LAYERS = [
    Layer(SKY, 0, 0.06),                 # sky + moon, barely moves
    Layer(build_far(), 190, 0.22),       # upper deck, hazy and soft
    Layer(build_mid(), 232, 0.40),       # main tier + roof trusses
    Layer(build_near(), 300, 0.66),      # lower bowl, tunnels, light towers
    Layer(build_pitch(), HORIZON - 8, 1.00, pad=False),
]
FORE = Layer(build_fore(), 800, 1.28)    # rail in front of the fighters


def build_vignette(depth=170, peak=140):
    v = pygame.Surface((WW, WH), pygame.SRCALPHA)
    depth = int(ks(depth))
    for i in range(depth):
        c = (0, 0, 0, int(peak * (1 - i / depth) ** 2.1))
        pygame.draw.line(v, c, (0, i), (WW, i))
        pygame.draw.line(v, c, (0, WH - 1 - i), (WW, WH - 1 - i))
        pygame.draw.line(v, c, (i, 0), (i, WH))
        pygame.draw.line(v, c, (WW - 1 - i, 0), (WW - 1 - i, WH))
    return v


VIGNETTE = build_vignette()
_flashes = []                    # crowd camera flashes: [x, y, life]
_beam_cache = {}


def _beam(i, spread, height, col, strength):
    """A floodlight shaft. Built premultiplied for the same reason as glow()."""
    # Quantise: the caller modulates strength with a sine, so an exact key
    # rebuilt this surface — 30 polygons plus two smoothscales — every frame.
    strength = round(strength, 2)
    key = (i, strength)
    b = _beam_cache.get(key)
    if b is None:
        b = pygame.Surface((int(spread * 2), int(height)), pygame.SRCALPHA)
        steps = 30
        for j in range(steps):
            u = j / steps
            w = spread * (0.16 + u * 0.84)
            f = strength * (1 - u) ** 1.6
            c = (int(col[0] * f), int(col[1] * f), int(col[2] * f), 255)
            if not (c[0] or c[1] or c[2]):
                continue
            pygame.draw.polygon(b, c, [
                (spread - w * 0.34, height * u), (spread + w * 0.34, height * u),
                (spread + w * 0.5, height * (u + 1.0 / steps)),
                (spread - w * 0.5, height * (u + 1.0 / steps))])
        b = soften(b, 3)
        _beam_cache[key] = b
    return b


def layer_offset(lay):
    ox = (cam.x - WW * 0.5) * (1.0 - lay.px)
    oy = (cam.y - WH * 0.5) * (1.0 - lay.px) * 0.5
    return ox, oy


def blit_layer(lay, view):
    """Blit only the part of a layer the camera will sample.

    Layers are wider than the world so panning never reveals an edge, but the
    camera only ever sees ~1270 of those 2200 columns. Clipping to the view
    turns every layer blit into at most one screenful.
    """
    ox, oy = layer_offset(lay)
    dx = (-PAD + ox) if lay.pad else ox
    dy = lay.y + oy
    sx = max(0, int(view.x - dx))
    sy = max(0, int(view.y - dy))
    w = min(lay.surf.get_width() - sx, int(view.right - dx - sx))
    h = min(lay.surf.get_height() - sy, int(view.bottom - dy - sy))
    if w > 0 and h > 0:
        screen.blit(lay.surf, (dx + sx, dy + sy), pygame.Rect(sx, sy, w, h))


def arena(t):
    """Draw the stadium, back to front, each layer at its own parallax rate."""
    view = cam.view_rect()
    for lay in LAYERS:
        blit_layer(lay, view)

    # --- volumetric floodlight beams from the towers down onto the turf
    near = LAYERS[3]
    ox, oy = layer_offset(near)
    for i, lx in enumerate((ks(103), WW - ks(101))):
        b = 0.86 + 0.14 * math.sin(t * 1.3 + i * 2.1)
        beam = _beam(i, ks(330), 520, (176, 186, 205), 0.075 * b)
        screen.blit(beam, (lx + ox - ks(330), 300 + oy),
                    special_flags=pygame.BLEND_RGBA_ADD)
        for j in range(3):
            cx, cy = lx + ox - ks(8) + j * ks(8), 306 + oy
            pygame.draw.circle(screen, shade((255, 246, 216), b), (int(cx), int(cy)), int(ks(3)))
            glow(cx, cy, int(ks(20) * b), (128, 126, 108))

    # --- crowd camera flashes: the single cheapest cue that a stadium is alive
    if random.random() < 0.55:
        _flashes.append([random.uniform(0, WW), random.uniform(210, 470), 1.0])
    for fl in _flashes[:]:
        fl[2] -= 0.14
        if fl[2] <= 0:
            _flashes.remove(fl)
            continue
        fx, fy = fl[0] + ox * 0.6, fl[1] + oy * 0.6
        glow(fx, fy, int(ks(13) * fl[2]), (255, 250, 225))
        pygame.draw.circle(screen, shade((255, 252, 235), fl[2]), (int(fx), int(fy)), 2)

    # --- drifting haze over the turf, so the air is not empty
    for i in range(3):
        hx = (t * (7 + i * 4) + i * 900) % (WW + ks(700)) - ks(350)
        glow(hx, HORIZON + 30 + i * 70, 190, (15, 21, 29))

    # --- LED perimeter running colour along the hoardings
    for i, x in enumerate(range(int(ks(40)), int(WW - ks(40)), int(ks(46)))):
        ph = (math.sin(t * 2.6 - i * 0.35) + 1) * 0.5
        c = (int(14 + 40 * ph), int(40 + 120 * ph), int(60 + 150 * ph))
        pygame.draw.rect(screen, c, (x, PITCH.bottom - 26, ks(30), ks(9)), border_radius=3)

    if not CFG.post.enabled:      # otherwise the post chain owns vignetting
        screen.blit(VIGNETTE, (view.x, view.y), view)


def arena_foreground():
    """The near barrier, drawn after the fighters so it occludes them."""
    blit_layer(FORE, cam.view_rect())


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
        dy = (target.y - ks(105)) - self.y
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
        tx, ty = self.t.x, self.t.y - ks(100)
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
        if q < ks(30):
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
            spawn(self.x + math.cos(a) * rr, self.y + math.sin(a) * rr * 0.45,
                  0, -random.uniform(20, 60), 0.6, self.c, 4, -60.0)

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
# PHASE C — camera
# --------------------------------------------------------------------------
class Camera:
    """Follows the midpoint of the fight, frames wider as the fighters separate,
    leads their velocity, and ignores small movement inside a deadzone."""

    def __init__(self):
        self.x = WW * 0.5
        self.y = GROUND_BOTTOM - 75
        self.view = CFG.camera.view_max
        self.shake_x = self.shake_y = 0.0
        self.seed = random.random() * 100

    def snap(self, a, b):
        self.x, self.y, self.view = self._want(a, b)

    def _want(self, a, b):
        c = CFG.camera
        mx = (a.x + b.x) * 0.5 + (a.vx + b.vx) * 0.5 * c.lookahead
        my = (a.y + b.y) * 0.5 + c.bias_y
        sep = abs(a.x - b.x) + abs(a.y - b.y) * 0.6
        view = max(c.view_min, min(c.view_max, sep * c.sep_gain + c.margin))
        return mx, my, view

    def update(self, dt, a, b):
        c = CFG.camera
        wx, wy, wview = self._want(a, b)
        # deadzone: the camera only chases once the target leaves the box
        dx, dy = wx - self.x, wy - self.y
        if abs(dx) > c.deadzone_x:
            self.x += (dx - math.copysign(c.deadzone_x, dx)) * min(1, c.follow * dt)
        if abs(dy) > c.deadzone_y:
            self.y += (dy - math.copysign(c.deadzone_y, dy)) * min(1, c.follow * dt)
        self.view += (wview - self.view) * min(1, c.zoom_follow * dt)

        # trauma-driven shake, smooth rather than white noise
        tr = shake * shake
        t = pygame.time.get_ticks() / 1000.0
        amp = tr * CFG.feel.shake_pixels
        self.shake_x = (math.sin(t * 27.3 + self.seed) * 0.6 +
                        math.sin(t * 11.7 + self.seed * 2) * 0.4) * amp
        self.shake_y = (math.sin(t * 23.1 + self.seed * 3) * 0.6 +
                        math.sin(t * 9.3 + self.seed * 4) * 0.4) * amp * 0.6

    def view_rect(self):
        vw = self.view * (1.0 - shake * shake * CFG.feel.shake_zoom)   # impact punch-in
        vh = vw * H / W
        x = self.x + self.shake_x - vw * 0.5
        y = self.y + self.shake_y - vh * 0.5
        x = max(0.0, min(WW - vw, x))
        y = max(0.0, min(WH - vh, y))
        return pygame.Rect(int(x), int(y), max(2, int(vw)), max(2, int(vh)))

    def apply(self, src, dst):
        r = self.view_rect()
        r.width = min(r.width, src.get_width() - r.x)
        r.height = min(r.height, src.get_height() - r.y)
        pygame.transform.smoothscale(src.subsurface(r), (W, H), dst)

    def to_screen(self, wx, wy):
        r = self.view_rect()
        return ((wx - r.x) * W / r.width, (wy - r.y) * H / r.height)


cam = Camera()


# --------------------------------------------------------------------------
# PHASE B — skeleton, inverse kinematics and procedural locomotion
#
# The figure is still a stick. Everything that makes it read as a body comes
# from motion: joints solved by IK rather than placed, feet that own a point on
# the ground and refuse to slide off it, and springs that let limbs overshoot
# and settle instead of snapping to a pose.
# --------------------------------------------------------------------------
def lerp(a, b, u):
    return a + (b - a) * u


def smoothstep(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def ik2(root, target, l1, l2, bend):
    """Two-bone IK.

    Returns (joint, end): the elbow/knee position, and the end effector after
    clamping to what the chain can actually reach — so a limb stretched past
    its length bends instead of detaching.
    """
    dx, dy = target[0] - root[0], target[1] - root[1]
    raw = math.hypot(dx, dy) or 1e-5
    ux, uy = dx / raw, dy / raw
    d = min(raw, l1 + l2 - 0.001)
    a = (d * d + l1 * l1 - l2 * l2) / (2 * d)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    joint = (root[0] + ux * a - uy * h * bend, root[1] + uy * a + ux * h * bend)
    return joint, (root[0] + ux * d, root[1] + uy * d)


def ik2_dir(root, target, l1, l2, px, py):
    """IK, choosing the bend whose joint lies further along (px, py).

    Picking the elbow/knee side by a preferred direction rather than a hard sign
    is what keeps knees pointing forward and elbows hanging down through every
    pose — a fixed sign flips to the wrong side as the target crosses the body.
    """
    ja, end = ik2(root, target, l1, l2, 1.0)
    jb, _ = ik2(root, target, l1, l2, -1.0)
    sa = (ja[0] - root[0]) * px + (ja[1] - root[1]) * py
    sb = (jb[0] - root[0]) * px + (jb[1] - root[1]) * py
    return (ja if sa >= sb else jb), end


class Spring:
    """Damped spring. Secondary motion lives here: overshoot, then settle."""
    __slots__ = ("v", "vel", "k", "d")

    def __init__(self, v=0.0, k=180.0, d=16.0):
        self.v = v; self.vel = 0.0; self.k = k; self.d = d

    def step(self, target, dt):
        self.vel += (target - self.v) * self.k * dt
        self.vel -= self.vel * min(1.0, self.d * dt)
        self.v += self.vel * dt
        return self.v

    def kick(self, amount):
        self.vel += amount


class Ragdoll:
    """Verlet points + distance constraints. Used for the death fall."""

    def __init__(self, pts, links, ground):
        self.p = [[x, y] for x, y in pts]
        self.old = [[x, y] for x, y in pts]
        self.links = links
        self.ground = ground

    def step(self, dt):
        g = CFG.motion.ragdoll_gravity * dt * dt
        damp = CFG.motion.ragdoll_damping
        for i, pt in enumerate(self.p):
            ox, oy = self.old[i]
            nx = pt[0] + (pt[0] - ox) * damp
            ny = pt[1] + (pt[1] - oy) * damp + g
            self.old[i] = [pt[0], pt[1]]
            pt[0], pt[1] = nx, ny
        for _ in range(4):                       # constraint relaxation
            for i, j, rest in self.links:
                a, b = self.p[i], self.p[j]
                dx, dy = b[0] - a[0], b[1] - a[1]
                d = math.hypot(dx, dy) or 1e-5
                corr = (d - rest) / d * 0.5
                a[0] += dx * corr; a[1] += dy * corr
                b[0] -= dx * corr; b[1] -= dy * corr
            for k, pt in enumerate(self.p):
                if pt[1] > self.ground:
                    pt[1] = self.ground
                    self.old[k][0] += (pt[0] - self.old[k][0]) * 0.45   # friction


class Skeleton:
    """Persistent pose state for one fighter.

    Joint positions are recomputed each frame, but *plant points*, spring
    velocities and stride phase persist — that persistence is what stops the
    walk being a sine wave and makes hits overshoot.
    """

    def __init__(self, f):
        self.f = f
        m = CFG.motion
        s = f.scale()
        self.lean = Spring(0.0, m.lean_k, m.lean_d)
        self.head = Spring(0.0, m.head_k, m.head_d)
        self.chest = Spring(0.0, m.chest_k, m.chest_d)
        # feet own world-space ground points; [x, y, lift]
        self.feet = [[f.x - m.stance_width * s, f.y, 0.0],
                     [f.x + m.stance_width * s, f.y, 0.0]]
        self.stance = 0              # index of the planted foot
        self.swing_t = 1.0           # 1.0 = both feet down
        self.swing_dur = CFG.motion.swing_time
        self.swing_from = (f.x, f.y)
        self.swing_to = (f.x, f.y)
        self.travel = 0.0
        self.breath = random.random() * 6.0
        self.sway = random.random() * 6.0
        self.acc = 0.0
        self.rag = None
        self.rag_blend = 0.0
        self.last_plant = None       # (x, y) consumed by the dust/audio systems

    # ---- events ---------------------------------------------------------
    def impulse(self, direction, power):
        """An incoming hit shoves the spine and snaps the head."""
        self.lean.kick(direction * power * CFG.motion.hit_impulse * 0.01)
        self.head.kick(direction * power * CFG.motion.hit_impulse * 0.016)
        self.chest.kick(-abs(power) * 0.18)

    def snap_feet(self):
        m = CFG.motion
        s = self.f.scale()
        self.feet[0] = [self.f.x - m.stance_width * s, self.f.y, 0.0]
        self.feet[1] = [self.f.x + m.stance_width * s, self.f.y, 0.0]
        self.swing_t = 1.0
        self.travel = 0.0

    def start_ragdoll(self, joints):
        order = ["head", "neck", "hip", "hand_b", "hand_f",
                 "elbow_b", "elbow_f", "knee_b", "knee_f", "foot_b", "foot_f"]
        pts = [joints[k] for k in order]
        idx = {k: i for i, k in enumerate(order)}
        pairs = [("head", "neck"), ("neck", "hip"),
                 ("neck", "elbow_b"), ("elbow_b", "hand_b"),
                 ("neck", "elbow_f"), ("elbow_f", "hand_f"),
                 ("hip", "knee_b"), ("knee_b", "foot_b"),
                 ("hip", "knee_f"), ("knee_f", "foot_f"),
                 ("head", "hip")]
        links = []
        for a, b in pairs:
            pa, pb = joints[a], joints[b]
            links.append((idx[a], idx[b], math.hypot(pb[0] - pa[0], pb[1] - pa[1])))
        self.rag = Ragdoll(pts, links, self.f.y)
        self.rag_blend = 0.0

    # ---- integration ----------------------------------------------------
    def update(self, dt):
        """Fixed-rate substeps so spring behaviour never depends on framerate."""
        self.acc += dt
        step = CFG.motion.substep
        n = 0
        while self.acc >= step and n < CFG.motion.max_substeps:
            self._step(step)
            self.acc -= step
            n += 1
        if n == CFG.motion.max_substeps:
            self.acc = 0.0

    def _step(self, dt):
        f = self.f
        m = CFG.motion
        s = f.scale()
        self.breath += dt * m.breath_rate
        self.sway += dt * 1.15

        if self.rag is not None:
            self.rag.step(dt)
            self.rag_blend = min(1.0, self.rag_blend + dt / max(m.ragdoll_blend, 1e-4))
            return

        # --- centre of mass leans into acceleration, springs back out of it
        target_lean = max(-m.lean_max, min(m.lean_max, f.vx * m.lean_per_speed))
        if f.stun > 0:
            target_lean += math.sin(self.breath * 9) * 5
        self.lean.step(target_lean, dt)

        # --- head tracks the opponent, within a limit
        other = e if f.player else p
        look = 0.0
        if other is not None and other is not f:
            look = max(-m.head_max, min(m.head_max, (other.x - f.x) * m.head_look * 0.1))
        self.head.step(look, dt)
        self.chest.step(0.0, dt)

        # --- locomotion: feet are placed by distance travelled, never by a clock
        speed = math.hypot(f.vx, f.vy)
        mid = (self.feet[0][0] + self.feet[1][0]) * 0.5
        if abs(f.x - mid) > m.replant_dist * s:      # dash / big knockback
            self.snap_feet()
            return

        if self.swing_t < 1.0:
            self.swing_t = min(1.0, self.swing_t + dt / max(self.swing_dur, 1e-4))
            sw = self.stance ^ 1
            foot = self.feet[sw]
            # Re-aim in flight so an accelerating fighter still lands the foot
            # underneath itself. Only while clearly airborne, so the planted
            # endpoints of the arc are never nudged.
            if speed > 12 and 0.15 < self.swing_t < 0.85:
                dirx, diry = f.vx / speed, f.vy / speed
                lat = (m.stance_width * s) * (1 if sw == 1 else -1)
                self.swing_to = (f.x + dirx * m.stride * s * m.plant_ahead - diry * lat,
                                 max(GROUND_TOP, min(GROUND_BOTTOM,
                                     f.y + diry * m.stride * s * m.plant_ahead * 0.6
                                     + dirx * lat * 0.4)))
            u = smoothstep(self.swing_t)
            foot[0] = lerp(self.swing_from[0], self.swing_to[0], u)
            foot[1] = lerp(self.swing_from[1], self.swing_to[1], u)
            foot[2] = math.sin(self.swing_t * math.pi) * m.step_lift * s
            if self.swing_t >= 1.0:
                foot[2] = 0.0
                self.last_plant = (foot[0], foot[1])   # footfall event
            return

        stride = m.stride * s
        reach = (m.thigh + m.shin) * s
        hip_y = f.y - m.hip_h * s
        anchor = self.feet[self.stance]
        leg_d = math.hypot(f.x - anchor[0], hip_y - anchor[1])

        if speed > 12:
            dirx, diry = f.vx / speed, f.vy / speed
        else:
            dirx = 1.0 if f.x >= (self.feet[0][0] + self.feet[1][0]) * 0.5 else -1.0
            diry = 0.0

        # Swing whichever foot is furthest BEHIND along the direction of travel
        # and anchor the other. Self-correcting: the planted foot is always the
        # forward one, so a stride can never be measured from a trailing foot.
        d0 = (f.x - self.feet[0][0]) * dirx + (f.y - self.feet[0][1]) * diry
        d1 = (f.x - self.feet[1][0]) * dirx + (f.y - self.feet[1][1]) * diry
        sw = 0 if d0 >= d1 else 1
        trailing = max(d0, d1)

        need_step = (leg_d > reach * m.max_extend
                     or (speed > 12 and trailing > stride * m.gap_trigger)
                     or (speed <= 12 and trailing > m.stance_width * s * 1.6))

        if need_step:
            self.stance = sw ^ 1
            self.swing_dur = max(0.070, min(m.swing_time,
                                            stride / max(speed, 1.0) * 0.45))
            lat = (m.stance_width * s) * (1 if sw == 1 else -1)
            self.swing_from = (self.feet[sw][0], self.feet[sw][1])
            self.swing_to = (f.x + dirx * stride * m.plant_ahead - diry * lat,
                             max(GROUND_TOP, min(GROUND_BOTTOM,
                                 f.y + diry * stride * m.plant_ahead * 0.6
                                 + dirx * lat * 0.4)))
            self.swing_t = 0.0
            self.travel = 0.0

    # ---- pose -----------------------------------------------------------
    def joints(self):
        """Solve the whole figure for this frame and hand back named points."""
        f = self.f
        m = CFG.motion
        s = f.scale()
        fc = f.facing

        phase = 0.0
        if f.atk > 0:
            phase = max(0.0, min(1.0, 1 - f.atk / max(f.atk_dur, 1e-3)))
        # anticipation -> strike -> recovery, instead of a plain sine
        if phase < 0.26:
            drive = -0.34 * smoothstep(phase / 0.26)        # wind up, pull back
        elif phase < 0.56:
            drive = lerp(-0.34, 1.0, smoothstep((phase - 0.26) / 0.30))
        else:
            drive = lerp(1.0, 0.0, smoothstep((phase - 0.56) / 0.44))
        atk = f.atk > 0
        kind = f.atype if atk else "idle"

        breath = math.sin(self.breath) * m.breath_amp * s
        shift = math.sin(self.sway) * m.weight_shift * s
        moving = math.hypot(f.vx, f.vy) > 12

        lean = self.lean.v
        if atk and kind == "punch":
            lean += fc * 13 * max(0.0, drive)
        elif atk and kind == "kick":
            lean -= fc * 15 * max(0.0, drive)
        elif atk:
            lean -= fc * 6 * max(0.0, drive)

        hip = [f.x + shift * 0.5, f.y - m.hip_h * s + self.chest.v * 0.4]
        # If a planted foot is further away than the leg can reach, sink the
        # hips until it can. A real body dips into a lunge rather than letting
        # the foot detach, and this makes non-detachment a guarantee, not a
        # tuning hope.
        reach_leg = (m.thigh + m.shin) * s * 0.985
        for _fx, _fy, _lift in self.feet:
            _dx = hip[0] - _fx
            if abs(_dx) < reach_leg:
                _lowest = _fy - math.sqrt(reach_leg * reach_leg - _dx * _dx)
                if hip[1] < _lowest:
                    hip[1] = _lowest
        neck = [f.x + lean * 0.55 + shift * 0.3,
                f.y - m.chest_h * s - breath * 0.35 + self.chest.v]
        head = [neck[0] + fc * 4 * s + self.head.v * 0.5,
                neck[1] - (m.head_h - m.chest_h) * s - breath * 0.2]

        J = {"hip": tuple(hip), "neck": tuple(neck), "head": tuple(head)}

        # --- legs: the planted foot is authoritative, the knee follows
        thigh, shin = m.thigh * s, m.shin * s
        kick_foot = None
        if atk and kind == "kick":
            chamber = smoothstep(min(1.0, phase / 0.32))
            kick_foot = (f.x + fc * (26 + 112 * max(0.0, drive)) * s,
                         f.y - (52 + 30 * chamber - 10 * max(0.0, drive)) * s)
        for i, tag in ((0, "b"), (1, "f")):
            fx, fy, lift = self.feet[i]
            target = (fx, fy - lift)
            hip_pt = (hip[0] + (-1 if i == 0 else 1) * 3 * s, hip[1])
            if kick_foot is not None and i == 1:
                target = kick_foot
            knee, foot = ik2_dir(hip_pt, target, thigh, shin, fc, 0.30)
            J["knee_" + tag] = knee
            J["foot_" + tag] = foot

        # --- arms
        ua, fa = m.upper_arm * s, m.forearm * s
        sh_b = (neck[0] - fc * m.shoulder_w * s, neck[1] + 7 * s)
        sh_f = (neck[0] + fc * m.shoulder_w * s, neck[1] + 6 * s)
        J["sh_b"], J["sh_f"] = sh_b, sh_f

        # Hands are placed relative to the shoulders, far enough out that the
        # arm keeps a natural bend. Tucked closer, the chain folds so hard the
        # elbow has nowhere to go but sideways, which reads as a chicken wing.
        guard_b = (sh_b[0] + fc * 27 * s, sh_b[1] - 17 * s + breath * 0.3)
        guard_f = (sh_f[0] + fc * 30 * s, sh_f[1] - 12 * s - breath * 0.3)
        if atk and kind == "punch":
            hand_f = (f.x + fc * (26 + 84 * max(0.0, drive)) * s, neck[1] + 3 * s)
            hand_b = (sh_b[0] + fc * 24 * s, sh_b[1] - 19 * s)
        elif atk and kind == "cast":
            hand_f = (f.x + fc * (26 + 34 * max(0.0, drive)) * s,
                      neck[1] - (24 + 30 * max(0.0, drive)) * s)
            hand_b = (sh_b[0] + fc * 26 * s, sh_b[1] - 4 * s)
        elif atk and kind == "kick":
            hand_f = (sh_f[0] + fc * 26 * s, sh_f[1] - 24 * s)
            hand_b = (sh_b[0] - fc * 22 * s, sh_b[1] - 6 * s)
        else:
            drift = 3 * s if moving else 0.0
            hand_f = (guard_f[0] + fc * drift, guard_f[1])
            hand_b = (guard_b[0] - fc * drift, guard_b[1])

        for sh, hand, tag in ((sh_b, hand_b, "b"), (sh_f, hand_f, "f")):
            elbow, end = ik2_dir(sh, hand, ua, fa, -fc * 0.25, 1.0)
            J["elbow_" + tag] = elbow
            J["hand_" + tag] = end

        if self.rag is not None:                 # blend animation -> physics
            order = ["head", "neck", "hip", "hand_b", "hand_f",
                     "elbow_b", "elbow_f", "knee_b", "knee_f", "foot_b", "foot_f"]
            u = smoothstep(self.rag_blend)
            for i, kname in enumerate(order):
                rx, ry = self.rag.p[i]
                ax, ay = J[kname]
                J[kname] = (lerp(ax, rx, u), lerp(ay, ry, u))
            J["sh_b"] = J["neck"]; J["sh_f"] = J["neck"]
        return J


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
        self.vx = 0.0; self.vy = 0.0                     # measured, drives lean + stride
        self.mvx = 0.0; self.mvy = 0.0                   # locomotion velocity
        self.buffer = None; self.buffer_t = 0.0          # queued attack input
        self.kx = 0.0                                    # knockback velocity
        self.skel = Skeleton(self)
        # modifiers (the shop writes these)
        self.dmg_mult = 1.0; self.ability_mult = 1.0; self.armor = 1.0
        self.lifesteal = 0.0; self.cdr = 1.0; self.ult_rate = 1.0; self.proj = 1.0

    # ---- geometry -------------------------------------------------------
    def scale(self):
        """Fighters further up the pitch stand smaller — cheap real depth."""
        return (CFG.arena.scale_near + ((self.y - GROUND_TOP) /
                (GROUND_BOTTOM - GROUND_TOP)) * CFG.arena.scale_range) * KS

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
        # the spine and head absorb the blow, overshoot, then settle
        other = e if self.player else p
        direction = 1.0
        if other is not None and other is not self:
            direction = 1.0 if self.x >= other.x else -1.0
        self.skel.impulse(direction, min(40.0, damage))
        floaters.append(FloatingText(self.x, self.y - 118, f"-{int(damage)}", RED))
        burst(self.x, self.y - 90 * self.scale(), self.b, 9, 130, 4)
        if self.player:
            add_flash(22)
        if self.hp <= 0:
            self.alive = False
            self.skel.start_ragdoll(self.skel.joints())
            self.skel.rag.p[1][0] += direction * 40      # throw the torso back
            self.skel.rag.p[0][0] += direction * 55
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
        """Input steers an acceleration, not a position.

        Reaching top speed takes a moment and stopping takes a moment, which is
        most of what makes the fighter feel like it has mass.
        """
        if self.stun > 0 or self.root > 0 or not self.alive:
            dx = dy = 0
        fe = CFG.feel
        top = self.speed * KS * (1.38 if self.buff > 0 else 1.0)
        if self.atk > 0:
            top *= 0.55                        # committed to a strike, not frozen
        q = math.hypot(dx, dy)
        if q:
            ux, uy = dx / q, dy / q
            acc = fe.accel * KS
            if ux * self.mvx < 0:              # reversing: bite harder
                acc *= fe.turn_boost
            self.mvx += ux * acc * dt
            self.mvy += uy * acc * dt * CFG.arena.depth_squash
            if self.atk <= 0 and abs(dx) > 0:
                self.facing = 1 if dx > 0 else -1
        else:
            damp = max(0.0, 1.0 - fe.friction * dt)
            self.mvx *= damp
            self.mvy *= damp

        sp = math.hypot(self.mvx, self.mvy / max(CFG.arena.depth_squash, 1e-3))
        if sp > top:                           # clamp to the spirit's top speed
            f = top / sp
            self.mvx *= f
            self.mvy *= f
        self.x += self.mvx * dt
        self.y += self.mvy * dt
        self.clamp()

    def clamp(self):
        lo, hi = MARGIN_X, WW - MARGIN_X
        if self.x < lo:
            self.x = lo; self.mvx = max(0.0, self.mvx)
        elif self.x > hi:
            self.x = hi; self.mvx = min(0.0, self.mvx)
        if self.y < GROUND_TOP:
            self.y = GROUND_TOP; self.mvy = max(0.0, self.mvy)
        elif self.y > GROUND_BOTTOM:
            self.y = GROUND_BOTTOM; self.mvy = min(0.0, self.mvy)

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
        elif self.alive and self.stun <= 0:
            # too early — remember it and fire the moment recovery ends
            self.buffer = kind
            self.buffer_t = CFG.feel.attack_buffer

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
        reach = ks(96 if self.atype == "punch" else 124) * self.scale() / KS
        if -ks(22) < front < reach and abs(dy) < ks(46):
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
            debris(hx, hy, self.b, self.facing)
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
            if dist(self, t) < ks(140) * self.scale() / KS:
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
            self.dash(t, d, ks(190))
        elif k == "spin":
            if dist(self, t) < ks(170) * self.scale() / KS:
                self.deal(t, damage, True)
                t.knock(1 if t.x > self.x else -1, 24)
            for a in range(18):
                ang = a / 18 * math.tau
                spawn(self.x + math.cos(ang) * 60, self.y - 70 + math.sin(ang) * 22,
                      math.cos(ang) * 140, math.sin(ang) * 60, 0.4, self.b, 5, 0.0)
            add_shake(4)
        elif k == "area":
            zones.append(Zone(t.x, t.y, ks(120), self.b, 2.6, d, t))
        elif k == "shield":
            self.shield = 4.5
            burst(self.x, self.y - 80, self.b, 20, 120, 5, grav=-150)
        elif k == "stun":
            if dist(self, t) < ks(330):
                shots.append(Shot(self, t, d, CYAN, speed=1100 * self.proj, homing=6, size=5))
                t.stun = 1.0
                floaters.append(FloatingText(t.x, t.y - 150, "STUNNED", CYAN, S))
        elif k in ("root", "arrest"):
            if dist(self, t) < ks(210):
                self.deal(t, damage, True)
                t.root = 1.7
                floaters.append(FloatingText(t.x, t.y - 150, "ROOTED", GOLD, S))
                for a in range(14):
                    ang = a / 14 * math.tau
                    spawn(t.x + math.cos(ang) * 42, t.y + math.sin(ang) * 16,
                          0, 0, 1.5, GOLD, 4, 0.0, 0.0)
        elif k == "heal":
            self.heal(damage)
        elif k == "hzone":
            zones.append(Zone(self.x, self.y, ks(110), GREEN, 3.0, damage, self, True, warn=0.0))
        elif k == "regen":
            self.buff = 5.0
            self.heal(damage)
        elif k == "life":
            if dist(self, t) < ks(160) * self.scale() / KS:
                got = self.deal(t, damage, True)
                self.heal(got * 0.6)
                for a in range(12):
                    spawn(t.x, t.y - 90, (self.x - t.x) * 1.6, -60, 0.6, self.b, 5, -40.0)
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
            spawn(ox + (self.x - ox) * f, oy - 80 + random.uniform(-22, 22),
                  -self.facing * 60, random.uniform(-30, 30), 0.35, self.b, 6, 40.0)
        self.skel.snap_feet()
        if abs(t.x - self.x) < ks(130) and abs(t.y - self.y) < ks(60):
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
                zones.append(Zone(t.x + random.uniform(-ks(130), ks(130)),
                                  t.y + random.uniform(-ks(50), ks(50)),
                                  ks(62), self.b, 2.4, d, t, warn=0.3 + j * 0.14))
        elif name in ("SWAT Raid", "Divine Judgment"):
            self.deal(t, damage, True)
            t.stun = 2.0
            zones.append(Zone(t.x, t.y, ks(140), self.b, 1.6, d * 0.25, t, warn=0.35))
        elif name == "Demon Rage":
            self.deal(t, damage, True)
            self.heal(35)
            self.buff = 5
            t.knock(self.facing, 24)
        elif name == "Sonic Speed":
            for _ in range(3):
                self.dash(t, damage / 3.0, ks(150))
            self.buff = 5
        elif name == "Inferno":
            zones.append(Zone(t.x, t.y, ks(165), self.b, 4.0, d, t, warn=0.4))
        elif name == "Earth Titan":
            self.shield = 6.0
            zones.append(Zone(self.x, self.y, ks(260), self.b, 0.9, d * 0.3, t, warn=0.3))
            if dist(self, t) < ks(300):
                self.deal(t, damage, True)
                t.knock(self.facing, 30)

    # ---- per-frame ------------------------------------------------------
    def up(self, dt):
        self.anim += dt

        if self.kx:                                    # knockback slide with friction
            self.x += self.kx * dt * 12
            self.kx *= max(0.0, 1 - dt * 9)
            if abs(self.kx) < 0.4:
                self.kx = 0.0
            self.clamp()

        # velocity is measured after every positional change this frame, so the
        # lean and the stride react to knockback as well as to input
        if dt > 1e-6:
            self.vx = (self.x - self.px) / dt
            self.vy = (self.y - self.py) / dt
        moved = math.hypot(self.x - self.px, self.y - self.py)
        self.moving = moved > 0.4
        self.px, self.py = self.x, self.y
        prev_plant = self.skel.last_plant
        self.skel.update(dt)
        if self.skel.last_plant is not prev_plant and self.skel.last_plant:
            fx, fy = self.skel.last_plant
            if math.hypot(self.vx, self.vy) > 40:
                dust(fx, fy, self.scale() / KS)
                sfx("step", 0.35)

        if self.buffer_t > 0:
            self.buffer_t -= dt
            if self.atk <= 0 and self.stun <= 0:
                kind, self.buffer, self.buffer_t = self.buffer, None, 0.0
                other = e if self.player else p
                if other is not None:
                    self.attack(other, kind)
            elif self.buffer_t <= 0:
                self.buffer = None

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
            spawn(self.x + random.uniform(-24, 24), self.y - random.uniform(0, 130),
                  0, -70, 0.5, GOLD, 4, -40.0)
        if self.atk > 0 and self.atype in ("punch", "kick") and not self.attack_landed:
            target = e if self.player else p
            if target:
                self.resolve_basic(target)

    # ---- rendering ------------------------------------------------------
    def draw(self):
        s = self.scale()
        fc = self.facing
        J = self.skel.joints()
        acc = self.b
        body = shade(self.c, 1.0)
        dark = shade(self.c, 0.55)      # limbs on the far side of the body
        lite = shade(self.c, 1.55)      # limbs on the near side — reads as lit
        if self.flinch > 0:
            k = 1.0 + self.flinch * 3
            body, dark, lite = shade(body, k), shade(dark, k), shade(lite, k)

        # --- contact shadow, thrown along the key light and shrinking as the
        # hips rise, so the figure reads as standing ON the pitch
        rest_h = CFG.motion.hip_h * s
        lift = max(0.0, (self.y - J["hip"][1]) - rest_h * 0.55) / max(rest_h, 1e-3)
        tight = max(0.35, 1.0 - lift * 0.8)
        sw, sh_ = int(112 * s * tight), int(28 * s * tight)
        if sw > 1 and sh_ > 1:
            sh_surf = pygame.Surface((sw, sh_), pygame.SRCALPHA)
            pygame.draw.ellipse(sh_surf, (0, 0, 0, int(CFG.light.shadow_alpha * tight)),
                                sh_surf.get_rect())
            throw = rest_h * CFG.light.shadow_len
            screen.blit(sh_surf, (J["hip"][0] - sw / 2 - LIGHT_DX * throw,
                                  self.y - sh_ * 0.35 - LIGHT_DY * throw * 0.16))

        # --- the figure is drawn at SS× into a scratch buffer and scaled down,
        # which anti-aliases the whole silhouette at once: no stair-stepping on
        # limbs, and no seams where capsules overlap.
        pad = 30 * s
        xs = [q[0] for q in J.values()]
        ys = [q[1] for q in J.values()]
        minx, miny = min(xs) - pad, min(ys) - pad
        w = min(FIG_W, int(max(xs) + pad - minx) + 2)
        h = min(FIG_H, int(max(ys) + pad - miny) + 2)
        if w < 4 or h < 4:
            return
        buf = _fig_buf.subsurface(pygame.Rect(0, 0, w * SS, h * SS))
        buf.fill((0, 0, 0, 0))

        def T(q):
            return ((q[0] - minx) * SS, (q[1] - miny) * SS)

        u = s * SS                                  # design units -> buffer px
        hip, neck, head = T(J["hip"]), T(J["neck"]), T(J["head"])

        def leg(tag, col):
            k_, f_ = T(J["knee_" + tag]), T(J["foot_" + tag])
            taper(buf, hip, k_, 13.5 * u, 11 * u, col)
            taper(buf, k_, f_, 11 * u, 8 * u, col)
            pygame.draw.circle(buf, acc, (int(f_[0]), int(f_[1])), int(6.5 * u))

        def arm(tag, col):
            sh_, el, hd = T(J["sh_" + tag]), T(J["elbow_" + tag]), T(J["hand_" + tag])
            taper(buf, sh_, el, 10.5 * u, 9 * u, col)
            taper(buf, el, hd, 9 * u, 7 * u, col)
            pygame.draw.circle(buf, acc, (int(hd[0]), int(hd[1])), int(7 * u))

        # Rim pass: the whole figure is drawn once offset toward the key light
        # in a brightened colour, then the real figure covers it — what peeks
        # out is an edge light along the lit side.
        hr = 15 * u
        rimf = CFG.light.rim
        if rimf > 0:
            rx = -LIGHT_DX * 2.6 * u * 0.5
            ry = -LIGHT_DY * 2.6 * u * 0.5
            rc = (min(255, int(CFG.light.color[0] * 0.55 + body[0] * 0.45)),
                  min(255, int(CFG.light.color[1] * 0.55 + body[1] * 0.45)),
                  min(255, int(CFG.light.color[2] * 0.55 + body[2] * 0.45)))
            rc = shade(rc, 0.55 + rimf * 0.6)
            off = lambda q: (q[0] + rx, q[1] + ry)
            for tag in ("b", "f"):
                taper(buf, off(hip), off(T(J["knee_" + tag])), 13.5 * u, 11 * u, rc)
                taper(buf, off(T(J["knee_" + tag])), off(T(J["foot_" + tag])),
                      11 * u, 8 * u, rc)
                taper(buf, off(T(J["sh_" + tag])), off(T(J["elbow_" + tag])),
                      10.5 * u, 9 * u, rc)
                taper(buf, off(T(J["elbow_" + tag])), off(T(J["hand_" + tag])),
                      9 * u, 7 * u, rc)
            taper(buf, off(hip), off(neck), 17 * u, 15 * u, rc)
            pygame.draw.circle(buf, rc, (int(head[0] + rx), int(head[1] + ry)), int(hr))

        leg("b", dark)
        arm("b", dark)
        taper(buf, hip, neck, 17 * u, 15 * u, body)                 # torso
        pygame.draw.circle(buf, acc, (int(hip[0]), int(hip[1])), int(8 * u))   # belt
        leg("f", lite)

        pygame.draw.circle(buf, SKIN, (int(head[0]), int(head[1])), int(hr))
        cap = pygame.Rect(0, 0, int(hr * 2), int(hr))                # hair
        cap.center = (int(head[0] - fc * 1.5 * u), int(head[1] - hr * 0.46))
        pygame.draw.ellipse(buf, dark, cap)
        pygame.draw.circle(buf, BLACK,
                           (int(head[0] + fc * 6 * u), int(head[1] + 2 * u)),
                           max(1, int(2.2 * u)))
        arm("f", lite)

        screen.blit(pygame.transform.smoothscale(buf, (w, h)), (minx, miny))

        if CFG.debug.skeleton:
            for name, q in J.items():
                pygame.draw.circle(screen, CYAN, ipt(q), 3)
            for foot in self.skel.feet:
                pygame.draw.circle(screen, RED if foot[2] < 0.5 else GOLD,
                                   ipt((foot[0], foot[1])), 5, 1)

        if not self.alive:
            return

        # --- glows and status, in world space (additive, must not be scaled)
        if self.atk > 0 and self.atype == "kick":
            fx, fy = J["foot_f"]
            if abs(fx - self.x) > 70 * s:
                glow(fx, fy, int(19 * s), acc)
        elif self.atk > 0 and self.atype in ("punch", "cast"):
            hx, hy = J["hand_f"]
            if abs(hx - self.x) > 55 * s:
                glow(hx, hy, int(15 * s), acc)

        bob = math.sin(self.skel.breath) * 3
        orb = (self.x + fc * 2, self.y - (196 * s + bob))
        glow(orb[0], orb[1], int(20 * s), acc)
        pygame.draw.circle(screen, acc, ipt(orb), int(8 * s))
        pygame.draw.circle(screen, WHITE, ipt((orb[0] - 2.5 * s, orb[1] - 2.5 * s)),
                           int(3 * s))

        if self.shield > 0:
            r = int(64 * s)
            a = int(58 + 40 * math.sin(self.anim * 7))
            srf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(srf, (acc[0], acc[1], acc[2], a), (r, r), r, 3)
            pygame.draw.circle(srf, (acc[0], acc[1], acc[2], 14), (r, r), r - 4)
            screen.blit(srf, (self.x - r, self.y - 92 * s - r))
        if self.stun > 0:
            hx, hy = J["head"]
            for j in range(3):
                a = self.anim * 7 + j * math.tau / 3
                pygame.draw.circle(screen, GOLD,
                                   ipt((hx + math.cos(a) * 26 * s,
                                        hy - 22 * s + math.sin(a) * 8 * s)), int(4 * s))
        if self.root > 0:
            pygame.draw.ellipse(screen, GOLD,
                                (self.x - 34 * s, self.y - 12 * s, 68 * s, 24 * s), 3)

        bw = 104
        bx, by = self.x - bw / 2, self.y - 226 * s
        pygame.draw.rect(screen, (8, 10, 15), (bx - 2, by - 2, bw + 4, 11), border_radius=3)
        frac = self.hp / self.maxhp
        pygame.draw.rect(screen, GREEN if frac > 0.4 else RED,
                         (bx, by, int(bw * frac), 7), border_radius=3)
        txt(self.n, (self.x, by - 14), SM, acc, True)


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


# --------------------------------------------------------------------------
# PHASE E — lighting and the post chain
#
# Order is bloom -> grade -> vignette -> grain -> chroma. Bloom reads from a
# separate emissive buffer rather than thresholding the frame: you cannot
# threshold cheaply in pygame, and drawing the emitters twice is both cheaper
# and more controllable than guessing which pixels were meant to be bright.
# --------------------------------------------------------------------------
LIGHT_DX = math.cos(math.radians(CFG.light.angle))
LIGHT_DY = math.sin(math.radians(CFG.light.angle))

EMIS = pygame.Surface((WW, WH)).convert()        # additive-only emitter buffer
_bloom_a = pygame.Surface((W // 4, H // 4)).convert()
_bloom_b = pygame.Surface((W // 8, H // 8)).convert()
_bloom_up = pygame.Surface((W, H)).convert()   # reused; never reallocated
_grade_mul = pygame.Surface((W, H)).convert()
_grade_add = pygame.Surface((W, H)).convert()
_grade_mul.fill(CFG.post.grade_gain)
_grade_add.fill(CFG.post.grade_lift)
UI_VIGNETTE = pygame.Surface((W, H), pygame.SRCALPHA)
_d = 150
for _i in range(_d):
    _c = (0, 0, 0, int(96 * (1 - _i / _d) ** 2.2))
    pygame.draw.line(UI_VIGNETTE, _c, (0, _i), (W, _i))
    pygame.draw.line(UI_VIGNETTE, _c, (0, H - 1 - _i), (W, H - 1 - _i))
    pygame.draw.line(UI_VIGNETTE, _c, (_i, 0), (_i, H))
    pygame.draw.line(UI_VIGNETTE, _c, (W - 1 - _i, 0), (W - 1 - _i, H))

# Grain is baked at its final intensity. set_alpha does nothing under
# BLEND_RGB_ADD — the same trap as the glow surfaces — so the strength has to
# live in the pixel values themselves.
_grain = []
_grain_amp = max(1, int(64 * CFG.post.grain))
for _t in range(CFG.post.grain_tiles):
    _g = pygame.Surface((W // 2, H // 2)).convert()
    _rng = random.Random(900 + _t)
    for _ in range(6000):
        _v = _rng.randint(0, _grain_amp)
        _g.set_at((_rng.randrange(W // 2), _rng.randrange(H // 2)), (_v, _v, _v))
    _grain.append(pygame.transform.smoothscale(_g, (W, H)))
_grain_i = [0]


def emit(x, y, radius, color):
    """Register a light source for the bloom pass."""
    if CFG.post.enabled:
        glow(x, y, radius, color, surf=EMIS)


def post_process(dst):
    """bloom -> grade -> vignette -> grain -> chroma, all intensity-gated."""
    c = CFG.post
    if not c.enabled:
        return
    if c.bloom > 0:
        r = cam.view_rect()
        r.width = min(r.width, EMIS.get_width() - r.x)
        r.height = min(r.height, EMIS.get_height() - r.y)
        pygame.transform.smoothscale(EMIS.subsurface(r), (W // 4, H // 4), _bloom_a)
        pygame.transform.smoothscale(_bloom_a, (W // 8, H // 8), _bloom_b)
        if c.bloom < 1.0:
            # scale the 160x90 buffer, not the 1280x720 one
            _bloom_b.fill((int(255 * c.bloom),) * 3, special_flags=pygame.BLEND_RGB_MULT)
        pygame.transform.smoothscale(_bloom_b, (W, H), _bloom_up)
        dst.blit(_bloom_up, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    dst.blit(_grade_mul, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
    dst.blit(_grade_add, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    if c.vignette:
        dst.blit(UI_VIGNETTE, (0, 0))
    if c.grain > 0:
        _grain_i[0] = (_grain_i[0] + 1) % len(_grain)
        dst.blit(_grain[_grain_i[0]], (0, 0), special_flags=pygame.BLEND_RGB_ADD)
    if c.chroma > 0:
        cp = dst.copy()
        off = max(1, int(c.chroma * 4))
        dst.blit(cp, (off, 0), special_flags=pygame.BLEND_RGB_ADD)
        dst.blit(cp, (-off, 0), special_flags=pygame.BLEND_RGB_ADD)


def result_overlay():
    """Drawn onto the UI over the camera's view of the arena."""
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
    global p, e, state, timer, shots, zones, floaters, result, intro, ko_timer, sim_acc
    p = Fighter(names[pick], ks(380), ks(505), 1)
    apply_loadout(p)
    e = Fighter(random.choice([n for n in names if n != p.n]), ks(900), ks(505), 0)
    state = "fight"
    sim_acc = 0.0
    timer = CFG.game.round_time
    intro = CFG.game.intro_time
    ko_timer = 0.0
    _p_live[0] = 0
    shots = []; zones = []; floaters = []
    result = ""
    cam.snap(p, e)
    start_ambient()
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
    desired = ks(240) if ranged else ks(105)
    low = e.hp / e.maxhp < 0.3

    if low and random.random() < 0.5:
        desired += ks(120)
    if q > desired + ks(30):
        e.move(dx, dy, dt)
    elif q < desired - ks(30):
        e.move(-dx, -dy * 0.6, dt)
    else:
        e.move(0, math.sin(pygame.time.get_ticks() / 700) * 0.6, dt)
    if abs(dx) > 2:
        e.facing = 1 if dx > 0 else -1

    if q < ks(140) and random.random() < dt * 1.5:
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


def update_particles(dt):
    """One pass: integrate, then compact live slots to the front."""
    n = _p_live[0]
    out = 0
    for i in range(n):
        a = particles[i]
        a[P_LIFE] -= dt
        if a[P_LIFE] <= 0:
            continue
        a[P_VY] += a[P_GRAV] * dt
        if a[P_DRAG]:
            d = 1.0 - a[P_DRAG] * dt
            if d < 0.0:
                d = 0.0
            a[P_VX] *= d
            a[P_VY] *= d
        a[P_X] += a[P_VX] * dt
        a[P_Y] += a[P_VY] * dt
        if a[P_KIND] == K_RAIN and a[P_Y] >= GROUND_BOTTOM + 40:
            if random.random() < CFG.fx.splash_chance:
                for _ in range(2):
                    spawn(a[P_X], GROUND_BOTTOM + 40, random.uniform(-55, 55),
                          random.uniform(-110, -40), 0.22, (120, 150, 170), 2, 900.0)
            continue
        if out != i:
            particles[out], particles[i] = particles[i], particles[out]
        out += 1
    _p_live[0] = out


def spawn_weather():
    w = CFG.fx.weather
    if w == "none":
        return
    want = CFG.fx.weather_count
    have = sum(1 for i in range(_p_live[0]) if particles[i][P_KIND] == K_RAIN)
    for _ in range(min(14, want - have)):
        x = random.uniform(-200, WW + 200)
        y = random.uniform(HORIZON - 260, HORIZON)
        if w == "rain":
            spawn(x, y, random.uniform(-70, -30), CFG.fx.rain_speed * random.uniform(0.85, 1.15),
                  3.0, (150, 178, 200), 1.6, 0.0, 0.0, K_RAIN)
        else:
            spawn(x, y, random.uniform(-30, 30), random.uniform(60, 120),
                  6.0, (208, 218, 232), 2.4, 0.0, 0.0, K_RAIN)


def spawn_ambient():
    if random.random() > CFG.fx.ambient_rate:
        return
    near = random.random() < 0.5
    x = random.uniform(0, WW)
    y = random.uniform(HORIZON - 40, GROUND_BOTTOM + 30)
    spawn(x, y, random.uniform(-14, 14), random.uniform(-22, -6),
          2.4 + random.random() * 2.2, (86, 104, 120) if near else (52, 64, 78),
          2.6 if near else 1.6, -3.0, 0.2, K_MOTE)


def update_world(dt):
    update_particles(dt)
    spawn_weather()
    spawn_ambient()
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


def draw_particles():
    for i in range(_p_live[0]):
        a = particles[i]
        f = a[P_LIFE] / a[P_MAX]
        kind = a[P_KIND]
        col = (int(a[P_R] * (0.4 + 0.6 * f)), int(a[P_G] * (0.4 + 0.6 * f)),
               int(a[P_B] * (0.4 + 0.6 * f)))
        if kind == K_RAIN:
            ln = CFG.fx.rain_len
            pygame.draw.line(screen, col, (a[P_X], a[P_Y]),
                             (a[P_X] - a[P_VX] * 0.012, a[P_Y] - ln), 1)
        elif kind == K_SMOKE or kind == K_DUST:
            r = max(1, int(a[P_SZ] * (1.6 - f * 0.6)))
            pygame.draw.circle(screen, col, (int(a[P_X]), int(a[P_Y])), r)
        else:
            r = max(1, int(a[P_SZ] * f))
            pygame.draw.circle(screen, col, (int(a[P_X]), int(a[P_Y])), r)


def draw_world(t):
    if CFG.post.enabled:
        EMIS.fill((0, 0, 0), cam.view_rect())   # only what bloom will sample
    arena(t)
    for z in zones:
        z.draw()
    draw_particles()
    for fighter in sorted((p, e), key=lambda z: z.y):   # depth ordering
        fighter.draw()
    for a in shots:
        a.draw()
    for a in floaters:
        a.draw()
    arena_foreground()


# --------------------------------------------------------------------------
# main frame
# --------------------------------------------------------------------------
def frame(events, dt, t):
    global state, pick, shop_sel, timer, intro, ko_timer, shake, flash, banner_t, sim_acc

    running = True
    for ev in events:
        if ev.type == pygame.QUIT:
            running = False
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_F3:
            CFG.debug.overlay = not CFG.debug.overlay      # works in every state
        elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_F4:
            CFG.debug.skeleton = not CFG.debug.skeleton
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
                    stop_ambient()
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
    shake = max(0.0, shake - dt * CFG.feel.trauma_decay)
    flash = max(0.0, flash - dt * 320)

    if state in ("fight", "ko"):
        # Fixed simulation rate: springs, IK and the ragdoll all behave the same
        # regardless of how fast the machine renders. The renderer then draws
        # whatever the last completed step produced.
        global sim_acc
        if state == "fight":
            intro = max(0.0, intro - dt)
            kk = pygame.key.get_pressed()
            ix = kk[pygame.K_d] - kk[pygame.K_a]
            iy = kk[pygame.K_s] - kk[pygame.K_w]
        else:
            ix = iy = 0
        scale = CFG.game.hitstop_timescale if (p.hitstop > 0 or e.hitstop > 0) else 1.0
        if state == "ko":
            scale = CFG.game.ko_timescale
        sim_acc += dt * scale
        step = 1.0 / CFG.feel.sim_hz
        n = 0
        while sim_acc >= step and n < CFG.feel.max_sim_steps:
            p.move(ix, iy, step)
            p.up(step)
            e.up(step)
            if state == "fight" and intro <= 0:
                ai(step)
            update_world(step)
            sim_acc -= step
            n += 1
        if n >= CFG.feel.max_sim_steps:
            sim_acc = 0.0

        if state == "fight":
            if intro <= 0:
                timer = max(0.0, timer - dt)
            if not p.alive or not e.alive or timer <= 0:
                state = "ko"
                ko_timer = CFG.game.ko_time
                say("K.O." if (not p.alive or not e.alive) else "TIME UP", 1.5)
                add_shake(10)
        else:
            ko_timer -= dt
            if ko_timer <= 0:
                finish()
        cam.update(dt, p, e)

    # ---- draw
    if state == "menu":
        target(UI)
        menu(t)
    elif state == "shop":
        target(UI)
        shop(t)
    elif state in ("fight", "ko"):
        target(WORLD)
        draw_world(t)
        target(UI)
        cam.apply(WORLD, UI)
        post_process(UI)
        hud()
        if state == "ko":
            ov = pygame.Surface((W, H), pygame.SRCALPHA)
            ov.fill((0, 0, 0, 90))
            UI.blit(ov, (0, 0))
    else:
        target(WORLD)
        arena(t)
        for fighter in sorted((p, e), key=lambda z: z.y):
            fighter.draw()
        arena_foreground()
        target(UI)
        cam.apply(WORLD, UI)
        post_process(UI)
        result_overlay()

    if banner_t > 0 and state in ("fight", "ko"):
        a = min(1.0, banner_t / 0.4)
        sc = 1.0 + (1 - min(1.0, banner_t)) * 0.0
        col = shade(GOLD, 0.4 + 0.6 * a)
        txt(banner, (W // 2, 300), XL if state == "ko" else B, col, True, surf=UI)

    if flash > 0:
        ov = pygame.Surface((W, H), pygame.SRCALPHA)
        ov.fill((255, 90, 90, int(min(110, flash))))
        UI.blit(ov, (0, 0))
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
        (f"particles {_p_live[0]:<4}/{CFG.fx.pool} shots {len(shots):<3} "
         f"zones {len(zones):<3} floaters {len(floaters)}", DIM),
        (f"glow cache {len(_glow_cache)}/{CFG.glow.cache_max}   state {state}", DIM),
    ]
    pad = pygame.Surface((330, 18 * len(rows) + 14), pygame.SRCALPHA)
    pad.fill((0, 0, 0, 165))
    UI.blit(pad, (10, 96))
    for i, (line, col) in enumerate(rows):
        txt(line, (20, 104 + i * 18), SM, col, surf=UI)


def main():
    running = True
    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        t = pygame.time.get_ticks() / 1000.0
        t0 = time.perf_counter()

        running = frame(pygame.event.get(), dt, t)
        if CFG.debug.overlay:
            debug_overlay()

        display.blit(UI, (0, 0))
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

    # start from a known loadout: a previous run that crashed mid-way can leave
    # items in the save file, and toggling those would turn them *off*
    equipped.clear()
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
    # must exceed the full round (round_time * 60) so a stalemate — e.g. a
    # self-healing Healer against a headless player that cannot press movement
    # keys — still reaches the timeout decision rather than failing the assert
    budget = int(CFG.game.round_time * 60) + 800
    for i in range(budget):
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
