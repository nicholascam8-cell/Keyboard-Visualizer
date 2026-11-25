"""Run with python -m unittest discover -s tests (NumPy required)."""
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace as Obj

import numpy as np
import spotify_rgb as rgb
from visualizer_settings import Settings


def zone(kind, led_ids, matrix=None):
    return Obj(type=Obj(name=kind), leds=[Obj(id=i) for i in led_ids], matrix_map=matrix)


class LayoutTests(unittest.TestCase):
    def test_mixed_zones_and_global_led_ids(self):
        device = Obj(name="Other keyboard", leds=list(range(8)), zones=[
            zone("SINGLE", [0]),
            zone("LINEAR", [1, 2]),
            zone("MATRIX", [3, 4, 5, 6], [[2, None, 0], [1, 0xFFFFFFFF, 3]]),
        ])
        x, y, solid = rgb.keyboard_layout(device)
        self.assertLess(x[5], x[3])
        self.assertLess(y[5], y[6])
        self.assertLess(x[1], x[2])
        self.assertEqual(np.flatnonzero(solid).tolist(), [0, 7])

    def test_missing_matrix_and_large_zones(self):
        x, y, solid = rgb.keyboard_layout(Obj(
            name="Unknown", leds=[0], zones=[zone("MATRIX", [0], None)]))
        self.assertEqual((x[0], y[0], solid[0]), (.5, .5, True))
        x = rgb.axis_positions(rgb.LINEAR_X, 200, 0, 255)
        self.assertTrue(np.all(np.diff(x) > 0))
        self.assertTrue(np.all((x >= 0) & (x <= 1)))

    def test_detects_keyboards_by_type_and_optional_name(self):
        mode = Obj(name="Direct", color_mode=Obj(name="PER_LED"))
        def device(idx, kind, name):
            return Obj(id=idx, name=name, type=Obj(name=kind), modes=[mode],
                       active_mode=0, leds=[0], zones=[])
        keyboards = [device(0, "KEYBOARD", "First"), device(1, "KEYBOARD", "Second")]
        client = Obj(devices=keyboards + [device(2, "MOUSE", "Third")])
        args = Settings(Path("unused-settings.txt"), {"mouse_name": "", "mic_name": ""}).effective()
        with patch.dict(sys.modules, {"openrgb": Obj(OpenRGBClient=lambda **_: client)}):
            _, lights = rgb.connect(args)
            self.assertEqual([item.device.name for item in lights], ["First", "Second"])
            args.keyboard_name = "sec"
            _, lights = rgb.connect(args)
            self.assertEqual([item.device.name for item in lights], ["Second"])

    @unittest.skipUnless(shutil.which("c++"), "Requires a development C++ compiler")
    def test_tables_match_original_cpp(self):
        src = Path(rgb.__file__).resolve().parent / "tools" / "grid_tables.cpp"
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "grid_tables"
            subprocess.run(["c++", "-std=c++17", str(src), "-o", str(exe)], check=True)
            output = subprocess.check_output([str(exe)], text=True)
        script = Path(rgb.__file__).read_text(encoding="utf-8")
        self.assertIn(output, script)
