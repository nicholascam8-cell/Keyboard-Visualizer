# SPDX-License-Identifier: GPL-2.0-only
"""NumPy port of the KeyboardVisualizer spectrum and color-pattern rendering.

Based on KeyboardVisualizerCommon/Visualizer.cpp and VisualizerDefines.h
by Adam Honse (CalcProgrammer1). See THIRD_PARTY.md for the source revision.
"""
import numpy as np

from visualizer_audio import BLOCK
from visualizer_colours import colour_field

WIDTH, HEIGHT = 256, 64
BAR_ROW, SOLID_ROW, FIRST_SPECTRUM_ROW = 0, 1, 2

_X = np.arange(WIDTH)
_Y = np.arange(HEIGHT)[:, None]
_BAR_DISTANCE = np.where(_X < 128, 127 - _X, _X - 128) / 128
_SOLIDS = np.array([
    [0, 0, 0], [255, 255, 255], [255, 0, 0], [255, 64, 0], [255, 255, 0],
    [0, 255, 0], [0, 255, 255], [0, 0, 255], [64, 0, 255],
])
_STATIC = (
    [5, 4, 2], [5, 1, 2], [7, 6, 1], [2, 1, 7], [2, 4, 5, 6, 7, 8], [8, 7, 6, 5, 4, 2],
)
_WINDOWS = (np.ones(BLOCK), np.hanning(BLOCK), np.hamming(BLOCK), np.blackman(BLOCK))


def draw_pattern(mode, phase, palette, colours="static", colour_phase=0.):
    if mode == 21 and colours in ("dynamic", "dynamic+"):
        return colour_field(colours, _X / (WIDTH - 1), _Y / (HEIGHT - 1), palette, colour_phase)
    if mode < len(_SOLIDS):
        return np.broadcast_to(_SOLIDS[mode], (HEIGHT, WIDTH, 3))
    if mode < 15 or mode == 21:
        colors = _SOLIDS[_STATIC[mode - 9]] if mode != 21 else palette
        if mode == 13:
            colors[-1] = [255, 0, 255]
        elif mode == 14:
            colors[0] = [255, 0, 255]
        steps = len(colors)
        row_indices = np.minimum(steps - 1, (steps * (1 - _Y[:, 0] / 63)).astype(int))
        rows = np.broadcast_to(colors[row_indices, None], (HEIGHT, WIDTH, 3)).copy()
        rows[BAR_ROW] = colors[np.minimum(steps - 1, (steps * _BAR_DISTANCE).astype(int))]
        return rows
    if mode in (15, 20):
        x = _X if mode == 15 else 1
        angle = (x / 255 - phase / 360) * (2 * np.pi)
        rgb = 127 * (1 + np.sin(np.asarray(angle)[..., None] + np.array([0, -2, 2]) * np.pi / 3))
    else:
        hue = phase
        if mode == 16:
            hue = phase + 256 - _X
        elif mode in (17, 18):
            center_y = 32 if mode == 17 else 64
            hue = phase + 180 + np.degrees(np.arctan2(_Y - center_y, _X - 128))
        # Fully saturated HSV at value 1.
        offset = (np.asarray(hue)[..., None] / 60 + [5, 3, 1]) % 6
        rgb = 255 * (1 - np.clip(np.minimum(offset, 4 - offset), 0, 1))
    return np.broadcast_to(rgb, (HEIGHT, WIDTH, 3))


class Spectrum:
    def __init__(self, rate):
        self.freqs = np.fft.rfftfreq(BLOCK, 1 / rate)
        self.columns = np.linspace(0, min(8000, rate / 2), WIDTH)
        self.weight_at = _X / WIDTH
        self.peak = np.zeros(WIDTH)
        self.filtered = np.zeros(WIDTH)
        self.phase = 0.
        self.colour_phase = 0.
        self.colour_speed = .05
        self.last_active = None

    def read(self, samples, dt, settings):
        window = _WINDOWS[settings.window_mode]
        wave = np.fft.rfft(samples * window[:, None], axis=0)
        magnitude = np.sqrt(np.mean(abs(wave) ** 2, axis=1)) * (2 / window.sum())
        magnitude[0] = magnitude[1]
        magnitudes = np.interp(self.columns, self.freqs, magnitude)
        normalization = settings.nrml_ofst + settings.nrml_scl * self.weight_at
        weighted = magnitudes * normalization * settings.amplitude * settings.gain
        target = .5 * np.log10(np.maximum(1.1 * weighted, 1e-12)) + .9 * weighted
        np.maximum(self.peak * (settings.decay / 100) ** (dt * 60),
                   np.clip(target, 0, 1), out=self.peak)
        width = settings.avg_size
        if settings.avg_mode == 0:
            starts = np.arange(0, WIDTH, width)
            sizes = np.diff(np.r_[starts, WIDTH])
            average = np.repeat(np.add.reduceat(self.peak, starts) / sizes, sizes)
        else:
            edges = np.r_[0, self.peak.cumsum()]
            left = np.maximum(0, _X - width)
            right = np.minimum(WIDTH, _X + width + 1)
            average = (edges[right] - edges[left]) / (right - left)
        self.filtered += settings.fltr_const * (average - self.filtered)

    def render(self, now, dt, settings, palette):
        self.phase = (self.phase + dt * 60 * settings.anim_speed / 100) % 360
        bass = float(self.filtered[5])
        speed = .05 + .7 * float(self.filtered.mean()) + .5 * bass
        self.colour_speed += (speed - self.colour_speed) * (-np.expm1(-dt / .25))
        self.colour_phase = (self.colour_phase + dt * self.colour_speed * settings.anim_speed / 100) % 100
        foreground = draw_pattern(settings.frgd_mode, self.phase, palette,
                                  settings.colours, self.colour_phase)
        background = draw_pattern(settings.bkgd_mode, self.phase, palette,
                                  settings.colours, self.colour_phase) * (settings.bkgd_bright / 100)
        if self.last_active is None or self.filtered.max() >= .0001:
            self.last_active = now
        timeout = settings.background_timeout / 100
        idle = now - self.last_active
        faded = bool(timeout and idle >= timeout)
        brightness = np.clip((idle - timeout) / (2 * timeout), 0, 1) if faded else bass
        if settings.silent_bkgd:
            body_background = background * (brightness if faded else 0)
        elif settings.reactive_bkgd:
            body_background = background * brightness
        else:
            body_background = background
        mask = self.filtered > (HEIGHT - _Y) / HEIGHT
        mask[BAR_ROW] = bass - .05 > _BAR_DISTANCE
        frame = np.where(mask[..., None], foreground, body_background)
        single = settings.single_color_mode
        if faded or single == 9:
            frame[SOLID_ROW] = background[SOLID_ROW]
        elif single < 9:
            color = _SOLIDS[single].copy()
            if single == 3:
                color[1] = 128
            elif single == 8:
                color[0] = 255
            frame[SOLID_ROW] = color * brightness
        elif single == 10:
            frame[SOLID_ROW] = background[SOLID_ROW] * brightness
        else:
            row = SOLID_ROW if settings.frgd_mode >= 15 else min(63, int(64 - brightness * 62))
            frame[SOLID_ROW] = foreground[row] * brightness
        return frame * settings.brightness
