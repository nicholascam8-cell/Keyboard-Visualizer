# SPDX-License-Identifier: GPL-2.0-only
"""Shared moving Kawarp palette fields for keyboard effects and the preview."""
import numpy as np


def colour_field(mode, x, y, palette, phase):
    x, y = np.broadcast_arrays(x, y)
    if mode == "dynamic":
        position = ((x + .10 * y - phase) % 1) * len(palette)
        index = position.astype(int) % len(palette)
        fraction = (position % 1)[..., None]
        return palette[index] * (1 - fraction) + palette[(index + 1) % len(palette)] * fraction

    # Periodic warping and orbiting colour pools produce fluid movement.
    # All time harmonics repeat at phase=100 to keep the clock wrap seamless.
    angle = phase * (2 * np.pi * .12)
    warped_x = x + .13 * np.sin(2 * np.pi * y + angle)
    warped_y = y + .13 * np.sin(2 * np.pi * x - angle)
    offsets = np.arange(len(palette)) * (2 * np.pi / len(palette))
    center_x = .5 + .42 * np.cos(angle + offsets)
    center_y = .5 + .42 * np.sin(2 * angle + offsets)
    distance = ((warped_x[..., None] - center_x) ** 2
                + (warped_y[..., None] - center_y) ** 2)
    weights = np.exp(-7 * distance)
    weights /= weights.sum(axis=-1, keepdims=True)
    return weights @ palette
