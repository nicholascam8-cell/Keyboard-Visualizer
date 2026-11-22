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


class Lights:
    def __init__(self, device, role, mode, rate):
        self.device, self.role = device, role
        self.live = mode.name.lower() == "direct" and mode.color_mode.name == "PER_LED"
        if device.modes[device.active_mode].name != mode.name:
            device.set_mode(mode)
        self.interval = 1 / rate
        self.next_write = 0
        self.previous = None
        self.ids, self.bands, self.rows, xs_all = [], [], [], []
        self.pattern = Pattern()
        self.mode, self.colours = "bottom-up", "static"
        if role == "Keyboard":
            for row, (ids, xs) in enumerate(zip(PHYSICAL_MAP, ROW_POSITIONS)):
                for led, x in zip(ids, xs):
                    if led < len(device.leds):
                        xs_all.append(x / 23)
                        self.ids.append(led)
                        self.bands.append(0 if x < 7 else 1 if x < 15 else 2)
                        self.rows.append(5 - row)
            self.ids, self.bands, self.rows = map(np.asarray, (self.ids, self.bands, self.rows))
            self.x = np.asarray(xs_all)
            self.y = 1 - self.rows / 5
            self.sequence = KeySequence(self.x, self.y)
        print(f"{role}: {device.name} • {mode.name} • " + ("live audio" if self.live else "album colour only"))

    def send(self, levels, palette, brightness, now, rms=0):
        from openrgb.utils import RGBColor
        if now < self.next_write:
            return
        self.next_write = now + self.interval
        if self.role == "Keyboard":
            pixels = self.pattern.render(self.mode, self.colours, self.x, self.y, self.bands, levels, palette)
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
        frame = np.zeros((len(self.device.leds), 3), dtype=np.uint8)
        frame[self.ids] = np.clip(pixels, 0, 255)
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
    for role, match in [("Keyboard", "apex 7"), ("Mouse", args.mouse_name), ("Mic", args.mic_name)]:
        candidates = []
        for device in client.devices:
            if not match or match.casefold() not in device.name.casefold():
                continue
            for mode in device.modes:
                live = mode.name.lower() == "direct" and mode.color_mode.name == "PER_LED" and device.leds
                static = mode.name.lower() in ("static", "fixed", "solid", "solid color", "solid colour", "direct") and mode.color_mode.name in ("PER_LED", "MODE_SPECIFIC")
                if live or (role != "Keyboard" and static):
                    candidates.append((bool(live), len(device.leds), device, mode))
        for _, _, device, mode in sorted(candidates, key=lambda c: c[:2], reverse=True):
            try:
                targets.append(Lights(device, role, mode, args.fps if role == "Keyboard" else min(args.fps, 24)))
                break
            except Exception as exc:
                print(role, device.name, exc)
        else:
            if match:
                print(f"{role}: no suitable controller for {match!r}")
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
