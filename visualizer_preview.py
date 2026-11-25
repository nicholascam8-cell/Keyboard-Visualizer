# SPDX-License-Identifier: GPL-2.0-only
"""Optional desktop preview for either rendering mode."""
import tkinter as tk

import numpy as np

from visualizer_spectrum import FIRST_SPECTRUM_ROW, HEIGHT, WIDTH


class Preview:
    def __init__(self, stop, minimized):
        self.root = tk.Tk()
        self.root.title("Keyboard Visualizer")
        self.root.configure(padx=18, pady=18, background="#151b25")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", stop.set)
        self.title = tk.Label(self.root, background="#151b25", foreground="#f0f4f8",
                              font=("TkDefaultFont", 15, "bold"), anchor="w")
        self.title.pack(fill="x", pady=(0, 12))
        self.screen = tk.Label(self.root, background="#000000", borderwidth=0)
        self.screen.pack()
        self.status = tk.Label(self.root, background="#151b25", foreground="#c0cbda", anchor="w")
        self.status.pack(fill="x", pady=(10, 0))
        if minimized:
            self.root.iconify()

    def draw(self, frame, mode, levels, audio_name):
        if mode == "spectrum":
            frame = frame[FIRST_SPECTRUM_ROW:]
            title = "Frequency spectrum"
        else:
            title = mode.replace("-", " ").title()
        pixels = np.clip(frame, 0, 255).astype(np.uint8)
        header = f"P6\n{WIDTH} {len(pixels)}\n255\n".encode("ascii")
        self.image = tk.PhotoImage(data=header + pixels.tobytes(), format="PPM").zoom(3, 3)
        self.screen.configure(image=self.image)
        self.title.configure(text=title)
        bass, mid, treble = (f"{value:.0%}" for value in levels)
        self.status.configure(text=f"{audio_name}   •   Bass {bass}   Mid {mid}   Treble {treble}")
        self.root.update()
