# SPDX-License-Identifier: GPL-2.0-only
# Modified 2026-11-24: OpenRGB keyboard discovery and zone mapping.
"""Spotify artwork → Kawarp colours; BlackHole audio → three keyboard level meters.
Place next to node_modules/@kawarp/core and run through start-keyboard-music.command.
"""
from dataclasses import dataclass
import argparse
import io
import os
from pathlib import Path
import shutil
import sys
import signal
import subprocess
import threading
import time
from collections import deque
from contextlib import ExitStack

import numpy as np

ROOT = Path(__file__).resolve().parent
BLOCK = 1024
FALLBACK = np.array([[60, 120, 230], [180, 75, 165], [65, 190, 140]], dtype=float)
# Physical LED IDs, including the navigation cluster and number pad.
PHYSICAL_MAP = [
    [37, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67],
    [48, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 41, 42, 38, 68, 69, 70, 94, 95, 96, 97],
    [39, 16, 22, 4, 17, 19, 24, 20, 8, 14, 15, 43, 44, 88, 71, 72, 73, 106, 107, 108, 98],
    [52, 0, 18, 3, 5, 6, 7, 9, 10, 11, 46, 47, 36, 103, 104, 105],
    [80, 25, 23, 2, 21, 1, 13, 12, 49, 50, 51, 84, 77, 100, 101, 102, 99],
    [79, 82, 81, 40, 85, 86, 87, 83, 75, 76, 74, 109, 110],
]
ROW_POSITIONS = [
    [0, 2, 3, 4, 5, 6.5, 7.5, 8.5, 9.5, 11, 12, 13, 14, 16, 17, 18],
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 17, 18, 20, 21, 22, 23],
    [0, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5, 13.5, 16, 17, 18, 20, 21, 22, 23],
    [0, 1.75, 2.75, 3.75, 4.75, 5.75, 6.75, 7.75, 8.75, 9.75, 10.75, 11.75, 12.75, 20, 21, 22],
    [0, 2.25, 3.25, 4.25, 5.25, 6.25, 7.25, 8.25, 9.25, 10.25, 11.25, 12.25, 17, 20, 21, 22, 23],
    [0, 1.25, 2.5, 3.75, 10.0, 11.25, 12.5, 13.75, 16, 17, 18, 20, 22],
]

# Render once on track change. Select representative *rendered pixels* so two
# different hues don't get averaged into a colour missing from the artwork.
KAWARP_JS = """
import {frame} from '@kawarp/core/frame';
import {readFileSync} from 'node:fs';
const src = readFileSync(0);
const pixels = frame(src, 64, 64, 24, 12, {
  blurPasses: 3, saturation: 1, tintIntensity: 0, dithering: 0, time: 0
});
process.stdout.write(Buffer.from(pixels));
"""
SPOTIFY = '''
if application "Spotify" is not running then return ""
tell application "Spotify"
    if player state is not playing then return ""
    return (name of current track) & linefeed & (artwork url of current track)
end tell
'''


def node_binary():
    for path in [shutil.which("node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"]:
        if path and os.access(path, os.X_OK):
            return path
    raise RuntimeError("Node.js is missing. Install Node.js, then install @kawarp/core@1.4.0 in the project folder.")


def kawarp_palette(node, rgba):
    proc = subprocess.run([node, "--input-type=module", "-e", KAWARP_JS],
                          input=rgba, capture_output=True, cwd=ROOT, timeout=12)
    if proc.returncode:
        raise RuntimeError("Kawarp failed. In the project folder run npm install @kawarp/core@1.4.0\n"
                           + proc.stderr.decode(errors="replace")[:500])
    pixels = np.frombuffer(proc.stdout, np.uint8).reshape(12, 24, 4)[:, :, :3]
    swatches = []
    for region in np.array_split(pixels, 3, axis=1):
        rgb = region.reshape(-1, 3).astype(float)
        # Ignore the darkest quarter where a cover uses a black border.
        strength = rgb.max(axis=1)
        candidates = rgb[strength >= np.percentile(strength, 25)]
        centre = np.median(candidates, axis=0)
        swatches.append(candidates[np.argmin(np.sum((candidates - centre) ** 2, axis=1))])
    return visible(np.array(swatches), pixels)


def watch_art(node, updates, stop):
    import requests
    from PIL import Image
    last_url = last_error = last_song = None
    while not stop.is_set():
        try:
            result = subprocess.run(["/usr/bin/osascript", "-e", SPOTIFY], capture_output=True,
                                    text=True, check=True, timeout=5)
            song, sep, url = result.stdout.strip().rpartition("\n")
            if sep and url and url != last_url:
                res = requests.get(url, timeout=(3, 6))
                res.raise_for_status()
                with Image.open(io.BytesIO(res.content)) as image:
                    rgba = image.convert("RGB").resize((64, 64), Image.Resampling.BOX).convert("RGBA")
                    colors = kawarp_palette(node, rgba.tobytes())
                cached = colors
                last_url = url
                print(f"Kawarp • {song} • " + " ".join("#%02x%02x%02x" % tuple(c.astype(int)) for c in colors))
            if sep and url == last_url and (song, url) != last_song:
                updates.append(cached)
                last_song = (song, url)
            last_error = None
        except Exception as exc:
            message = str(getattr(exc, "stderr", "") or exc).strip()
            if message != last_error:
                print("Spotify artwork unavailable:", message)
                last_error = message
        stop.wait(2)


class Levels:
    """Measure stereo band power. Scale each band against recent musical peaks."""
    def __init__(self, rate, gain):
        self.window = np.hanning(BLOCK)
        hz = np.fft.rfftfreq(BLOCK, 1 / rate)
        self.bins = [(hz >= a) & (hz < b) for a, b in [(40, 250), (250, 2400), (2400, 14000)]]
        self.reference = np.array([-35., -42., -50.])
        self.envelope = np.zeros(3)
        self.gain = gain

    def read(self, samples, dt):
        rms = float(np.sqrt(np.mean(samples ** 2)))
        if rms < 0.0003:
            target = np.zeros(3)
        else:
            spectrum = np.fft.rfft(samples * self.window[:, None], axis=0)
            power = np.mean(np.abs(spectrum) ** 2, axis=1) / BLOCK ** 2
            db = 10 * np.log10([max(1e-12, power[band].sum()) for band in self.bins])
            self.reference = np.maximum(db, np.maximum(-65, self.reference - 2.4 * dt))
            target = np.clip((db - self.reference + 22) / 22, 0, 1) ** 2.1
            target = np.clip(target * self.gain, 0, 1)
        tau = np.where(target > self.envelope, 0.025, 0.105)
        self.envelope += (target - self.envelope) * (1 - np.exp(-dt / tau))
        return self.envelope, rms


SETTINGS = ROOT / ".visualizer-settings"
MODES = ("bottom-up", "horizontal", "top-down", "central", "whole", "random")
OPTIONS = {"mode": MODES, "colours": ("static", "dynamic")}

def setting(key, default):
    try:
        value = (SETTINGS / key).read_text().strip()
        if key == "fps":
            return int(value) if 1 <= int(value) <= 480 else default
        return value if value in OPTIONS[key] else default
    except (OSError, ValueError):
        return default

def control():
    parser = argparse.ArgumentParser(description="Change live visualizer settings")
    parser.add_argument("key", choices=["mode", "colours", "fps", "status"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    if args.key == "status":
        for key, default in [("mode", "bottom-up"), ("colours", "static"), ("fps", 40)]:
            print(key, setting(key, default))
        return
    value = args.value or ""
    valid = value.isascii() and value.isdecimal() and 1 <= int(value) <= 480 if args.key == "fps" else value in OPTIONS[args.key]
    if not valid:
        parser.error("Choose " + (", ".join(OPTIONS[args.key]) if args.key in OPTIONS else "1–480"))
    SETTINGS.mkdir(exist_ok=True)
    temp = SETTINGS / f".{args.key}-{os.getpid()}"
    temp.write_text(value)
    temp.replace(SETTINGS / args.key)
    print(args.key, value)

def visible(swatches, pixels):
    pool = pixels.reshape(-1, 3).astype(float)
    peak = pool.max(axis=1)
    candidates = pool[peak >= max(10, peak.max() * .4)]
    for i, color in enumerate(swatches):
        if max(color) < 5 or (max(color) < 24 and peak.max() > 3*max(color)):
            if len(candidates):
                color = candidates[i * (len(candidates) - 1) // 2]
            else:
                color = FALLBACK[i]
        swatches[i] = color * max(1, 235 / max(color))
    return swatches

@dataclass
class Effect:
    kind: str
    origin: np.ndarray
    angle: float
    speed: float
    strength: float
    progress: float = 0.


class Pattern:
    def __init__(self):
        self.rng = np.random.default_rng()
        self.phase = self.clock = self.energy = self.drive = 0.
        self.previous = np.zeros(3)
        self.hits = deque()
        self.cooldown = self.spawn_time = 0.
        self.effects = []
        self.driving = False

    def advance(self, levels, dt, rms):
        self.clock += dt
        loud = float(np.clip((20*np.log10(max(rms, 1e-6))+57)/48, 0, 1))
        target = loud * (.45 + .55*levels.max())
        self.energy += (target-self.energy) * (-np.expm1(-dt/.14))
        flux = float(np.maximum(levels-self.previous, 0) @ [.5, .15, .35])
        self.previous = levels.copy()
        hit = flux > .055 and self.clock >= self.cooldown and loud > .10
        if hit:
            self.hits.append(self.clock)
            self.cooldown = self.clock + .14
        while self.hits and self.hits[0] < self.clock - 1.2:
            self.hits.popleft()
        # Either repeated transients or sustained, loud broadband sound can
        # accelerate motion. A brief peak alone should only lift brightness.
        density = min(1., max(0, len(self.hits) - 1) / 2)
        broadband = float(np.mean(levels))
        sustained = float(np.clip((loud * broadband - .52) / .32, 0, 1))
        evidence = max(density * loud, sustained)
        response = .28 if evidence > self.drive else .70
        self.drive += (evidence - self.drive) * (-np.expm1(-dt / response))
        was_driving = self.driving
        self.driving = self.drive > (.40 if was_driving else .64)
        accent = self.driving and not was_driving
        self.phase = (self.phase + dt*(.05+.7*levels.mean()+.5*levels[0])) % 100
        self.effects = [e for e in self.effects if e.progress < 1]
        power = min(1., self.energy * (.46 + .86*self.drive))
        for e in self.effects:
            e.progress += dt*(.18+.83*power+.4*self.drive)*e.speed
        capacity = min(5, 2 + int(4*power))
        if (accent or hit or self.clock >= self.spawn_time) and loud > .04 and len(self.effects) < capacity:
            bold = accent or (self.driving and self.rng.random() < power*(.25+.75*self.drive))
            kind = self.rng.choice(("line","burst") if bold else ("wave","ripple"))
            boost = self.rng.uniform(1.22,1.55) if self.driving else 1.08
            boost = boost if self.rng.random()<.5 else 1.
            self.effects.append(Effect(kind, self.rng.random(2), self.rng.uniform(-np.pi,np.pi),
                                       self.rng.uniform(.65,1.1), boost*(.28+.7*power)))
            self.spawn_time = self.clock + 1.55-1.35*power

    def random_light(self, x, y, levels):
        # Screen blend accumulates contributions without replacing earlier effects.
        remaining = np.ones_like(x)
        for e in self.effects:
            dx, dy = x-e.origin[0], (y-e.origin[1])*.55
            along = dx*np.cos(e.angle)+dy*np.sin(e.angle)
            radius = np.hypot(dx, dy)
            p = min(e.progress, 1.)
            if e.kind == "wave":
                shape = (.5+.5*np.cos(6*along+1.3*np.sin(4*y)-6*p))**1.5
            elif e.kind == "ripple":
                shape = (.5+.5*np.cos(14*radius-8*p))**2
            elif e.kind == "line":
                shape = np.exp(-30*np.abs(along-(p-.5)*1.5))
            else:
                shape = np.exp(-12*np.abs(radius-p))*(1-.55*p)
            order = np.clip(.5 + along*.55, 0, 1)
            fade = smooth((p-.12*order)/.18) * smooth((1-p-.18*order)/.20)
            remaining *= 1-np.minimum(.98, shape*fade*e.strength)
        gate = min(1., levels.max()*5)
        light = (1-remaining)*min(1., self.energy*(1.1+.45*self.drive))
        return light*gate

    def render(self, mode, colours, x, y, bands, levels, palette):
        strength = levels[bands]
        if mode == "horizontal":
            strength = levels[np.minimum((y * 3).astype(int), 2)]
            light = np.clip((strength - x) * 18 + strength, 0, 1)
        elif mode == "central":
            distance = np.hypot((x-.5)*2, (y-.5)*1.2) / 1.17
            light = np.clip((strength-distance)*7 + strength, 0, 1)
        elif mode == "whole":
            light = np.full_like(x, levels.max())
        elif mode == "random":
            light = self.random_light(x, y, levels)
        else:
            distance = y if mode == "top-down" else 1-y
            light = np.clip(6*strength - 5*distance, 0, 1)
        colors = palette[bands]
        if colours == "dynamic":
            pos = ((x + y*.10 - self.phase) % 1)*3
            index = pos.astype(int) % 3
            fraction = (pos % 1)[:, None]
            colors = palette[index]*(1-fraction) + palette[(index+1)%3]*fraction
        # A single multiplier per key preserves channel ratios even at full output.
        if mode == "random":
            light = np.minimum(1.28 * light, 255 / np.maximum(colors.max(axis=1), 1))
        return np.clip(colors * light[:, None], 0, 255)


def smooth(value):
    value = np.clip(value, 0, 1)
    return value * value * (3 - 2 * value)


class KeySequence:
    """Blend from the last displayed frame, with a different start time per key."""
    def __init__(self, x, y):
        self.x, self.y = x, y
        self.rng = np.random.default_rng()
        self.frame = np.zeros((len(x), 3))
        self.playing = False
        self.quiet_since = None
        self.started = None

    def begin(self, now, playing):
        self.playing = playing
        self.started = now
        self.source = self.frame.copy()
        style = self.rng.integers(3)
        if style == 0:
            angle = self.rng.uniform(0, 2*np.pi)
            order = self.x*np.cos(angle) + self.y*.4*np.sin(angle)
        elif style == 1:
            cx, cy = self.rng.random(2)
            order = np.hypot(self.x-cx, .4*(self.y-cy))
            if self.rng.random() < .5:
                order = -order
        else:
            order = self.rng.random(len(self.x))
        self.order = (order-order.min()) / max(np.ptp(order), 1e-9)
        self.duration = .85 if playing else 1.35

    def blend(self, pixels, now):
        target = pixels if self.playing else np.zeros_like(self.frame)
        if self.started is None:
            self.frame = target
        else:
            progress = (now-self.started)/self.duration
            weight = smooth((progress-.65*self.order)/.35)[:, None]
            self.frame = self.source*(1-weight) + target*weight
            if progress >= 1:
                self.started = None
                self.frame = target
        return self.frame

    def update(self, pixels, rms, now):
        if rms >= .0003:
            self.quiet_since = None
            if not self.playing:
                self.begin(now, True)
        elif self.playing:
            if self.quiet_since is None:
                self.quiet_since = now
            if now-self.quiet_since < .14:
                return self.frame
            self.begin(now, False)
        return self.blend(pixels, now)


# Generated by tools/grid_tables.cpp. See THIRD_PARTY.md.
MATRIX_X = (
    "",
    "80",
    "40c0",
    "2a7fd4",
    "2060a0e0",
    "194c7fb2e5",
    "153f6993bde7",
    "12365a7ea2c6ea",
    "1030507090b0d0f0",
    "0e2a46627e9ab6d2ee",
    "0e2a46627e7e9ab6d2ee",
    "0c253e57707089a2bbd4ed",
    "0b223950677e7e95acc3daf1",
    "0a1f34495e7373889db2c7dcf1",
    "091c2f4255687b7b8ea1b4c7daed",
    "091b2d3f516375758799abbdcfe1f3",
    "08192a3b4c5d6e7f7f90a1b2c3d4e5f6",
    "0818283848586878788898a8b8c8d8e8f8",
    "07162534435261707f7f8e9dacbbcad9e8f7",
    "071523313f4d5b6977778593a1afbdcbd9e7f5",
    "0613202d3a4754616e7b7b8895a2afbcc9d6e3f0",
    "06121e2a36424e5a6672727e8a96a2aebac6d2deea",
    "06121e2a36424e5a66727e7e8a96a2aebac6d2deeaf6",
    "05101b26313c47525d6873737e89949faab5c0cbd6e1ec",
    "05101b26313c47525d68737e7e89949faab5c0cbd6e1ecf7",
    "050f19232d37414b555f6973737d87919ba5afb9c3cdd7e1eb",
    "050f19232d37414b555f69737d7d87919ba5afb9c3cdd7e1ebf5",
    "040d161f28313a434c555e67707079828b949da6afb8c1cad3dce5",
    "040d161f28313a434c555e67707979828b949da6afb8c1cad3dce5ee",
    "040d161f28313a434c555e67707979828b949da6afb8c1cad3dce5eef7",
    "040c141c242c343c444c545c646c74747c848c949ca4acb4bcc4ccd4dce4",
    "040c141c242c343c444c545c646c74747c848c949ca4acb4bcc4ccd4dce4ec",
    "040c141c242c343c444c545c646c747c7c848c949ca4acb4bcc4ccd4dce4ecf4",
    "040c141c242c343c444c545c646c747c7c848c949ca4acb4bcc4ccd4dce4ecf4fc",
    "030a11181f262d343b424950575e656c73737a81888f969da4abb2b9c0c7ced5dce3",
    "030a11181f262d343b424950575e656c73737a81888f969da4abb2b9c0c7ced5dce3ea",
    "030a11181f262d343b424950575e656c737a7a81888f969da4abb2b9c0c7ced5dce3eaf1",
    "030a11181f262d343b424950575e656c737a7a81888f969da4abb2b9c0c7ced5dce3eaf1f8",
    "03090f151b21272d33393f454b51575d63696f6f757b81878d93999fa5abb1b7bdc3c9cfd5db",
    "03090f151b21272d33393f454b51575d63696f6f757b81878d93999fa5abb1b7bdc3c9cfd5dbe1",
    "03090f151b21272d33393f454b51575d63696f75757b81878d93999fa5abb1b7bdc3c9cfd5dbe1e7",
    "03090f151b21272d33393f454b51575d63696f75757b81878d93999fa5abb1b7bdc3c9cfd5dbe1e7ed",
    "03090f151b21272d33393f454b51575d63696f757b7b81878d93999fa5abb1b7bdc3c9cfd5dbe1e7edf3",
    "03090f151b21272d33393f454b51575d63696f757b7b81878d93999fa5abb1b7bdc3c9cfd5dbe1e7edf3f9",
    "02070c11161b20252a2f34393e43484d52575c61666b6b70757a7f84898e93989da2a7acb1b6bbc0c5cacfd4",
    "02070c11161b20252a2f34393e43484d52575c61666b6b70757a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9",
    "02070c11161b20252a2f34393e43484d52575c61666b7070757a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9de",
    "02070c11161b20252a2f34393e43484d52575c61666b7070757a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9dee3",
    "02070c11161b20252a2f34393e43484d52575c61666b7075757a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9dee3e8",
    "02070c11161b20252a2f34393e43484d52575c61666b7075757a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9dee3e8ed",
    "02070c11161b20252a2f34393e43484d52575c61666b70757a7a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9dee3e8edf2",
    "02070c11161b20252a2f34393e43484d52575c61666b70757a7a7f84898e93989da2a7acb1b6bbc0c5cacfd4d9dee3e8edf2f7",
    "02070c11161b20252a2f34393e43484d52575c61666b70757a7f7f84898e93989da2a7acb1b6bbc0c5cacfd4d9dee3e8edf2f7fc",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e6266666a6e72767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6cace",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6a6e72767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6a6e72767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e6e72767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6da",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e6e72767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dade",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e7272767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e7272767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e7276767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6ea",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e7276767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6eaee",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e72767a7a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6eaeef2",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e72767a7a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6eaeef2f6",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e72767a7e7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6eaeef2f6fa",
)
MATRIX_Y = (
    "",
    "21",
    "1130",
    "0c2034",
    "09182736",
    "0814202c38",
    "07111b252f39",
    "060e161e262e36",
    "050c131a21282f36",
    "050b11171d23292f35",
    "050b11171d23292f353b",
    "04090e13181d22272c3136",
    "04090e13181d22272c31363b",
    "04080c1014181c2024282c3034",
    "04080c1014181c2024282c303438",
    "04080c1014181c2024282c3034383c",
    "0306090c0f1215181b1e2124272a2d30",
)
LINEAR_X = (
    "",
    "80",
    "40c0",
    "418082",
    "2060a0e0",
    "2c568081ab",
    "153f6a95bfea",
    "2343638084a4c4",
    "1030507090b0d0f0",
    "1d364f6880829bb4cd",
    "0c253f58728ca5bfd8f2",
    "1a2f44596e808499aec3d8",
    "0a1f344a5f748a9fb4cadff4",
    "182a3c4e6072808597a9bbcddf",
    "091b2d3f526476899badbfd2e4f6",
    "17273747576777808898a8b8c8d8e8",
    "08182838485868788898a8b8c8d8e8f8",
    "162432404e5c6a78808795a3b1bfcddbe9",
    "071523313f4e5c6a788795a3b1bfcedceaf8",
    "15212d3945515d697580828e9aa6b2becad6e2",
    "06121f2c3946525f6c7986929facb9c6d2dfecf9",
    "15202b36414c57626d7880848f9aa5b0bbc6d1dce7",
    "05101c27333f4a56626d7985909ca7b3bfcad6e2edf9",
    "151f29333d47515b656f7980848e98a2acb6c0cad4dee8",
    "050f1a252f3a454f5a656f7a858f9aa5afbac5cfdae5effa",
    "151e273039424b545d666f7880828b949da6afb8c1cad3dce5",
    "040d17212b353f48525c66707a848d97a1abb5bfc8d2dce6f0fa",
    "161f28313a434c555e67707982808c959ea7b0b9c2cbd4dde6eff8",
    "040d161f28313a444d565f68717a848d969fa8b1bac4cdd6dfe8f1fa",
    "161e262e363e464e565e666e767e80878f979fa7afb7bfc7cfd7dfe7ef",
    "040c151d262e373f485059616a727b848c959da6aeb7bfc8d0d9e1eaf2fb",
    "171f272f373f474f575f676f777f87809098a0a8b0b8c0c8d0d8e0e8f0f8ff",
    "040c141c242c343c444c545c646c747c848c949ca4acb4bcc4ccd4dce4ecf4fc",
    "171e252c333a41484f565d646b72798080888f969da4abb2b9c0c7ced5dce3eaf1",
    "030a1219212830373f464e555d646c737b838a9299a1a8b0b7bfc6ced5dde4ecf3fb",
    "181f262d343b424950575e656c737a81888090979ea5acb3bac1c8cfd6dde4ebf2f9ff",
    "030a11181f262d343b434a51585f666d747b838a91989fa6adb4bbc3cad1d8dfe6edf4fb",
    "181e242a30363c42484e545a60666c72787e80858b91979da3a9afb5bbc1c7cdd3d9dfe5eb",
    "030910171d242b32383f464d535a61686e757c838990979da4abb2b8bfc6cdd3dae1e8eef5fc",
    "191f252b31373d43494f555b61676d73797f85808c92989ea4aab0b6bcc2c8ced4dae0e6ecf2f8",
    "03090f161c23292f363c43494f565c63696f767c83898f969ca3a9afb6bcc3c9cfd6dce3e9eff6fc",
    "1a20262c32383e444a50565c62686e747a80868c8093999fa5abb1b7bdc3c9cfd5dbe1e7edf3f9ffff",
    "03090f151b21272d33393f464c52585e646a70767c83898f959ba1a7adb3b9bfc6ccd2d8dee4eaf0f6fc",
    "1a1f24292e33383d42474c51565b60656a6f74797e8084898e93989da2a7acb1b6bbc0c5cacfd4d9dee3e8",
    "02070d13191f242a30363c42474d53595f646a70767c82878d93999fa4aab0b6bcc2c7cdd3d9dfe4eaf0f6fc",
    "1b20252a2f34393e43484d52575c61666b70757a7f84808a8f94999ea3a8adb2b7bcc1c6cbd0d5dadfe4e9eef3",
    "02070d12181d23282e34393f444a4f555b60666b71767c82878d92989da3a8aeb4b9bfc4cacfd5dbe0e6ebf1f6fc",
    "1c21262b30353a3f44494e53585d62676c71767b80858a8090959a9fa4a9aeb3b8bdc2c7ccd1d6dbe0e5eaeff4f9fe",
    "02070c12171c22272c32373c42474c52575c62676c72777c82878c92979ca2a7acb2b7bcc2c7ccd2d7dce2e7ecf2f7fc",
    "1d22272c31363b40454a4f54595e63686d72777c81868b9080969ba0a5aaafb4b9bec3c8cdd2d7dce1e6ebf0f5faffffff",
    "02070c11161b20252a30353a3f44494e53595e63686d72777c82878c91969ba0a5aab0b5babfc4c9ced3d9dee3e8edf2f7fc",
    "1d2125292d3135393d4145494d5155595d6165696d7175797d8082868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2",
    "02060b10151a1f24292e33383d42464b50555a5f64696e73787d82868b90959a9fa4a9aeb3b8bdc2c6cbd0d5dadfe4e9eef3f8fd",
    "1e22262a2e32363a3e42464a4e52565a5e62666a6e72767a7e8280878b8f93979b9fa3a7abafb3b7bbbfc3c7cbcfd3d7dbdfe3e7eb",
    "02060b1014191e23272c31363a3f44494d52575c60656a6f73787d82868b9094999ea3a7acb1b6babfc4c9cdd2d7dce0e5eaeff3f8fd",
    "1f23272b2f33373b3f43474b4f53575b5f63676b6f73777b7f8387808c9094989ca0a4a8acb0b4b8bcc0c4c8ccd0d4d8dce0e4e8ecf0f4",
    "02060b0f14181d22262b2f34383d42464b4f54585d62666b6f74787d82868b8f94989da2a6abafb4b8bdc2c6cbcfd4d8dde2e6ebeff4f8fd",
    "2024282c3034383c4044484c5054585c6064686c7074787c8084888c809195999da1a5a9adb1b5b9bdc1c5c9cdd1d5d9dde1e5e9edf1f5f9fd",
    "02060a0f13181c2025292e32363b3f44484d51555a5e63676b7074797d82868a8f93989ca0a5a9aeb2b6bbbfc4c8cdd1d5dadee3e7ebf0f4f9fd",
    "2125292d3135393d4145494d5155595d6165696d7175797d8185898d9180969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6eaeef2f6fafeffff",
    "02060a0e13171b1f24282c3035393d42464a4e53575b5f64686c7075797d82868a8e93979b9fa4a8acb0b5b9bdc2c6caced3d7dbdfe4e8ecf0f5f9fd",
    "22262a2e32363a3e42464a4e52565a5e62666a6e72767a7e82868a8e9296809b9fa3a7abafb3b7bbbfc3c7cbcfd3d7dbdfe3e7ebeff3f7fbffffffffff",
    "02060a0e12161a1e23272b2f33373b3f44484c5054585c6065696d7175797d82868a8e92969a9ea3a7abafb3b7bbbfc4c8ccd0d4d8dce0e5e9edf1f5f9fd",
    "23272b2f33373b3f43474b4f53575b5f63676b6f73777b7f83878b8f93979b80a0a4a8acb0b4b8bcc0c4c8ccd0d4d8dce0e4e8ecf0f4f8fcffffffffffffff",
    "02060a0e12161a1e22262a2e32363a3e42464a4e52565a5e62666a6e72767a7e82868a8e92969a9ea2a6aaaeb2b6babec2c6caced2d6dadee2e6eaeef2f6fafe",
)


def axis_positions(table, count, first, last):
    if count < len(table):
        return (np.frombuffer(bytes.fromhex(table[count]), np.uint8).astype(float) - first) / (last - first)
    # Above the precomputed sizes, use evenly spaced cell centres.
    return (np.arange(count, dtype=float) + .5) / count


def keyboard_layout(device):
    count = len(device.leds)
    x = np.full(count, .5)
    y = np.full(count, .5)
    solid = np.ones(count, dtype=bool)
    has_matrix = False
    for zone in device.zones:
        kind = zone.type.name
        if kind == "MATRIX" and zone.matrix_map:
            grid = zone.matrix_map
            width = max(map(len, grid), default=0)
            xs = axis_positions(MATRIX_X, width, 0, 255)
            ys = axis_positions(MATRIX_Y, len(grid), 2, 63)
            for row, cells in enumerate(grid):
                for col, local in enumerate(cells):
                    if local is None or not 0 <= local < len(zone.leds):
                        continue
                    led = zone.leds[local].id
                    if 0 <= led < count:
                        x[led], y[led], solid[led] = xs[col], ys[row], False
                        has_matrix = True
        elif kind == "LINEAR":
            xs = axis_positions(LINEAR_X, len(zone.leds), 0, 255)
            for led, pos in zip(zone.leds, xs):
                if 0 <= led.id < count:
                    x[led.id], solid[led.id] = pos, False
    # Keep the known full-size Apex profile for firmware without a matrix.
    if not has_matrix and device.name.casefold() == "steelseries apex 7" and count == 111:
        for row, (ids, positions) in enumerate(zip(PHYSICAL_MAP, ROW_POSITIONS)):
            for led, pos in zip(ids, positions):
                x[led], y[led], solid[led] = pos / 23, row / 5, False
    return x, y, solid


class Lights:
    def __init__(self, device, role, mode, rate):
        self.device, self.role = device, role
        self.live = mode.name.lower() == "direct" and mode.color_mode.name == "PER_LED"
        if device.modes[device.active_mode].name != mode.name:
            device.set_mode(mode)
        self.interval = 1 / rate
        self.next_write = 0
        self.previous = None
        self.pattern = Pattern()
        self.mode, self.colours = "bottom-up", "static"
        if role == "Keyboard":
            self.x, self.y, self.solid = keyboard_layout(device)
            self.bands = np.clip((self.x * 3).astype(int), 0, 2)
            self.sequence = KeySequence(self.x, self.y)
        print(f"{role}: {device.name} • {mode.name} • " + ("live audio" if self.live else "album colour only"))

    def send(self, levels, palette, brightness, now, rms=0):
        from openrgb.utils import RGBColor
        if now < self.next_write:
            return
        self.next_write = now + self.interval
        if self.role == "Keyboard":
            pixels = self.pattern.render(self.mode, self.colours, self.x, self.y, self.bands, levels, palette)
            if self.solid.any():
                pixels[self.solid] = palette[np.argmax(levels)] * levels.max()
            self.write_keys(self.sequence.update(pixels * brightness, rms, now))
            return
        else:
            if self.live:
                band = int(np.argmax(levels))
                color = palette[band] * levels[band] * brightness
            else:
                color = palette[1] * brightness
            key = tuple(map(int, color))
            if key != self.previous:
                self.device.set_color(RGBColor(*key), fast=self.live)
        self.previous = key

    def write_keys(self, pixels):
        from openrgb.utils import RGBColor
        frame = np.clip(pixels, 0, 255).astype(np.uint8)
        key = frame.tobytes()
        if key != self.previous:
            self.device.set_colors([RGBColor(*c) for c in frame.tolist()], fast=True)
            self.previous = key

    def finish_keys(self):
        seq = self.sequence
        if seq.frame.any():
            if seq.playing:
                seq.begin(time.monotonic(), False)
            while seq.started is not None:
                self.write_keys(seq.blend(None, time.monotonic()))
                if seq.started is not None:
                    time.sleep(1/40)
        self.write_keys(np.zeros_like(seq.frame))


def connect(args):
    from openrgb import OpenRGBClient
    client = OpenRGBClient()
    targets = []
    for role, match in [("Keyboard", args.keyboard_name), ("Mouse", args.mouse_name), ("Mic", args.mic_name)]:
        candidates = []
        if role != "Keyboard" and not match:
            continue
        for device in client.devices:
            if role == "Keyboard" and device.type.name != "KEYBOARD":
                continue
            if match and match.casefold() not in device.name.casefold():
                continue
            for mode in device.modes:
                live = mode.name.lower() == "direct" and mode.color_mode.name == "PER_LED" and device.leds
                static = mode.name.lower() in ("static", "fixed", "solid", "solid color", "solid colour", "direct") and mode.color_mode.name in ("PER_LED", "MODE_SPECIFIC")
                if live or (role != "Keyboard" and static):
                    candidates.append((bool(live), len(device.leds), device, mode))
        configured = set()
        for _, _, device, mode in sorted(candidates, key=lambda c: c[:2], reverse=True):
            if device.id in configured:
                continue
            try:
                targets.append(Lights(device, role, mode, args.fps if role == "Keyboard" else min(args.fps, 24)))
                configured.add(device.id)
                if role != "Keyboard":
                    break
            except Exception as exc:
                print(role, device.name, exc)
        if not configured:
            print(f"{role}: no suitable controller for {match or role!r}")
    return client, targets


def main():
    import pyaudio
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--palette", choices=["auto"], default="auto")
    parser.add_argument("--brightness", type=float, default=1.0)
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--fps", "--keyboard-fps", type=int, default=40)
    parser.add_argument("--audio-device-name", default="BlackHole 2ch")
    parser.add_argument("--audio-device", type=int)
    parser.add_argument("--audio", choices=["mono", "stereo"], default="stereo")
    parser.add_argument("--list-audio-devices", action="store_true")
    parser.add_argument("--keyboard-name", default="", help="Select keyboards by name substring (default: all direct RGB keyboards)")
    parser.add_argument("--mouse-name", default="G203")
    parser.add_argument("--mic-name", default="888-000534")
    args = parser.parse_args()
    if not (0 <= args.brightness <= 1 and 0 < args.gain <= 10 and 1 <= args.fps <= 480):
        parser.error("Use brightness 0–1, gain above 0 through 10 and fps 1–480.")
    with ExitStack() as cleanup:
        audio = pyaudio.PyAudio()
        cleanup.callback(audio.terminate)
        inputs = [audio.get_device_info_by_index(i) for i in range(audio.get_device_count())]
        inputs = [d for d in inputs if d["maxInputChannels"] > 0]
        if args.list_audio_devices:
            for d in inputs:
                print(d["index"], d["name"])
            return
        matches = [d for d in inputs if (d["index"] == args.audio_device if args.audio_device is not None
                   else args.audio_device_name.casefold() in d["name"].casefold())]
        if len(matches) != 1:
            raise RuntimeError("Audio input missing or ambiguous. Run with --list-audio-devices and pass --audio-device INDEX.")
        dev = matches[0]
        rate = int(dev["defaultSampleRate"])
        channels = min(2 if args.audio == "stereo" else 1, int(dev["maxInputChannels"]))
        node = node_binary()
        # Verify the actual renderer before opening hardware.
        kawarp_palette(node, bytes([64, 128, 192, 255]) * 4096)
        buffer = deque(maxlen=1)
        def capture(data, count, timing, status):
            if count == BLOCK:
                buffer.append(data)
            return None, pyaudio.paContinue
        stream = audio.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=BLOCK, stream_callback=capture)
        cleanup.callback(stream.close)
        print(f"Audio: {dev['name']} • {rate} Hz. Waiting for Spotify artwork.")
        for attempt in range(6):
            client, targets = connect(args)
            if any(t.role == "Keyboard" for t in targets) or attempt == 5:
                break
            getattr(client, "disconnect", lambda: None)()
            time.sleep(1)
        cleanup.callback(getattr(client, "disconnect", lambda: None))
        if not targets:
            raise RuntimeError("No matching RGB controllers. Check OpenRGB's Devices tab.")
        stop = threading.Event()
        cleanup.callback(stop.set)
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        colors = deque(maxlen=1)
        threading.Thread(target=watch_art, args=(node, colors, stop), daemon=True).start()
        meter = Levels(rate, args.gain)
        palette = FALLBACK
        last_audio = previous = time.monotonic()
        next_log = next_settings = previous
        fps = args.fps
        silent = np.zeros((BLOCK, channels))
        levels = np.zeros(3)
        rms = 0
        try:
            while not stop.is_set():
                now = time.monotonic()
                dt = min(0.25, now - previous)
                previous = now
                if now >= next_settings:
                    fps = setting("fps", args.fps)
                    mode = setting("mode", "bottom-up")
                    colours = setting("colours", "static")
                    for target in targets:
                        target.mode, target.colours = mode, colours
                        target.interval = 1 / (fps if target.role == "Keyboard" else min(24, fps))
                    next_settings = now + .35
                if colors:
                    palette = colors.pop()
                    for target in targets:
                        if target.role == "Keyboard":
                            target.pattern = Pattern()
                            if target.sequence.playing:
                                target.sequence.begin(now, True)
                if buffer:
                    samples = np.frombuffer(buffer.pop(), np.int16).reshape(BLOCK, channels) / 32768.
                    levels, rms = meter.read(samples, min(now - last_audio, 0.25))
                    last_audio = now
                elif now - last_audio > 0.15:
                    levels, rms = meter.read(silent, dt)
                for target in targets:
                    try:
                        if target.role == "Keyboard":
                            target.pattern.advance(levels, dt, rms)
                        target.send(levels, palette, args.brightness, now, rms)
                    except Exception as exc:
                        print(f"{target.device.name}: write failed: {exc}")
                        target.next_write = now + 2
                if now >= next_log:
                    print(f"rms={rms:.4f}  bass/mid/high=" + "/".join(f"{v:.0%}" for v in levels))
                    next_log = now + 2
                stop.wait(max(0, 1 / fps - (time.monotonic() - now)))
        finally:
            for target in targets:
                if target.live:
                    try:
                        if target.role == "Keyboard":
                            target.finish_keys()
                            continue
                        target.next_write = 0
                        target.send(np.zeros(3), palette, 0, time.monotonic())
                    except Exception:
                        pass


if __name__ == "__main__":
    try:
        if sys.argv[1:2] in (["mode"], ["colours"], ["fps"], ["status"]):
            control()
        else:
            main()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        raise SystemExit(f"Visualizer: {error}")
