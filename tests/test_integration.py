import contextlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import Mock, patch

import numpy as np

import spotify_rgb as rgb
from visualizer_artwork import FALLBACK, kawarp_palette
from visualizer_audio import BLOCK, RATE, open_audio, select_input
from visualizer_settings import PATTERNS, ROOT, Settings, command_line, output_fps
from visualizer_spectrum import BAR_ROW, FIRST_SPECTRUM_ROW, HEIGHT, SOLID_ROW, WIDTH, Spectrum, draw_pattern


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.profile = Path(self.tmp.name) / "settings.txt"
        self.terminal = (sys.executable, str(ROOT / "spotify_rgb.py"), "--settings", str(self.profile))

    def test_upstream_format_flags_and_a_live_terminal_command(self):
        self.profile.write_bytes(b"; upstream settings\r\namplitude=125\r\nmode=bottom-up\r\n"
                                 b"audio_device_idx=3\r\ndelay=20\r\nfrgd_mode=17\r\n")
        settings, _ = command_line(["--settings", str(self.profile), "mode=spectrum",
                                    "amplitude=150", "--amplitude", "175", "--audio-device-name", "USB"])
        start = settings.effective()
        self.assertEqual((start.mode, start.amplitude, start.frgd_mode), ("spectrum", 175, 17))
        self.assertEqual(output_fps(start), 50)
        self.assertIsNone(start.audio_device_idx)
        subprocess.run((*self.terminal, "fps", "75"), check=True, capture_output=True)
        self.assertEqual((settings.reload().mode, settings.effective().amplitude), ("spectrum", 175))
        result = subprocess.run((*self.terminal, "mode", "bottom-up"), check=True, text=True,
                                capture_output=True)
        self.assertEqual(result.stdout.strip(), "mode bottom-up")
        self.assertIn("; upstream settings", self.profile.read_text())
        self.assertEqual(settings.reload().mode, "bottom-up")
        self.profile.write_text("mode=whole\naudio_device_idx=20\nfps=60\n")
        live = settings.reload()
        self.assertEqual((live.mode, live.audio_device_idx, output_fps(live)), ("whole", None, 60))
        self.profile.write_text("fps=-1\n")
        with self.assertRaisesRegex(ValueError, "at least 1"):
            settings.reload()
        self.assertEqual(settings.effective().mode, "whole")

    def test_legacy_profile_is_used_until_an_explicit_value_exists(self):
        legacy = self.profile.parent / ".visualizer-settings"
        legacy.mkdir()
        (legacy / "colours").write_text("dynamic")
        (legacy / "fps").write_text("96")
        settings = Settings(self.profile, legacy_dir=legacy)
        self.assertEqual(settings.effective().colours, "dynamic")
        self.assertEqual(output_fps(settings.effective()), 96)
        self.profile.write_text("fps=auto\ndelay=25\n")
        self.assertEqual(output_fps(settings.reload()), 40)

    def test_demo_switches_renderer_when_profile_changes(self):
        profile = self.profile
        settings, args = command_line(["--settings", str(profile), "--demo", "--palette", "off"])
        rendered = []

        class Preview:
            def __init__(self, stop, minimized):
                self.stop = stop
                self.root = Obj(destroy=lambda: None)

            def draw(self, frame, mode, levels, audio_name):
                rendered.append((mode, frame.shape, frame.max()))
                if len(rendered) == 1:
                    profile.write_text("mode=spectrum\nbkgd_mode=0\n")
                if mode == "spectrum" or len(rendered) == 50:
                    self.stop.set()

        with (patch.dict(sys.modules, {"visualizer_preview": Obj(Preview=Preview)}),
              patch.object(rgb, "open_audio") as audio, patch.object(rgb, "connect") as connect,
              contextlib.ExitStack() as cleanup):
            rgb.run(settings, args, cleanup)
            audio.assert_not_called()
            connect.assert_not_called()
        self.assertEqual(rendered[0][0], "bottom-up")
        mode, shape, peak = rendered[-1]
        self.assertEqual((mode, shape), ("spectrum", (HEIGHT, WIDTH, 3)))
        self.assertGreater(peak, 0)


class RendererTests(unittest.TestCase):
    def test_spectrum_finds_a_tone_and_uses_distinct_output_rows(self):
        settings = Settings(Path("unused-settings.txt"), {
            "bkgd_mode": 0, "frgd_mode": 1, "avg_size": 4, "amplitude": 140,
        }).effective()
        spectrum = Spectrum(RATE)
        wave = .3 * np.sin(np.arange(BLOCK) * 2 * np.pi * 840 / RATE)
        signal = np.column_stack([wave, -wave])
        spectrum.read(signal, BLOCK / RATE, settings)
        self.assertLess(abs(spectrum.columns[np.argmax(spectrum.filtered)] - 840), 150)
        frame = spectrum.render(1., BLOCK / RATE, settings, FALLBACK)
        self.assertTrue(frame[FIRST_SPECTRUM_ROW:].any())
        self.assertFalse(frame[[BAR_ROW, SOLID_ROW]].any())
        for index in range(len(PATTERNS)):
            pattern = draw_pattern(index, 45, FALLBACK)
            self.assertEqual(pattern.shape, (HEIGHT, WIDTH, 3))
            self.assertTrue(np.all((pattern >= 0) & (pattern <= 255)))

    def test_device_selector_and_spectrum_sampling(self):
        leds = [Obj(id=0), Obj(id=1)]
        direct = Obj(name="Direct", color_mode=Obj(name="PER_LED"))
        fixed = Obj(name="Static", color_mode=Obj(name="MODE_SPECIFIC"))
        device = Obj(id=4, name="RGB strip", type=Obj(name="LEDSTRIP"), active_mode=0,
                     modes=[direct], leds=leds, set_colors=Mock(),
                     zones=[Obj(type=Obj(name="LINEAR"), leds=leds[:1]),
                            Obj(type=Obj(name="SINGLE"), leds=leds[1:])])
        keyboard = Obj(id=5, name="Fixed keyboard", type=Obj(name="KEYBOARD"), active_mode=0,
                       modes=[fixed], leds=[], zones=[], set_color=Mock())
        settings = Settings(Path("unused-settings.txt"), {"device": "4,fixed", "mode": "spectrum"}).effective()
        client = Obj(devices=[keyboard, device], disconnect=Mock())
        with patch.object(rgb, "open_rgb", return_value=client):
            _, targets = rgb.connect(settings)
        frame = np.zeros((HEIGHT, WIDTH, 3))
        frame[BAR_ROW] = [20, 30, 40]
        frame[SOLID_ROW] = [50, 60, 70]
        for light in targets:
            light.send(settings, np.zeros(3), FALLBACK, frame, 1., 0., 0., 40.)
        colors = device.set_colors.call_args.args[0]
        self.assertEqual([(c.red, c.green, c.blue) for c in colors], [(20, 30, 40), (50, 60, 70)])
        keyboard.set_color.assert_called_once()

    @unittest.skipUnless(shutil.which("node") and (ROOT / "node_modules/@kawarp/core").is_dir(),
                         "Requires Node.js and npm ci")
    def test_original_kawarp_renderer(self):
        palette = kawarp_palette(shutil.which("node"), bytes([64, 128, 192, 255]) * 4096)
        self.assertEqual(palette.shape, (3, 3))
        self.assertTrue(np.all(palette[:, 2] > palette[:, 1]))
        self.assertTrue(np.all(palette[:, 1] > palette[:, 0]))
        self.assertTrue(np.isfinite(palette).all())


class ArtworkStartupTests(unittest.TestCase):
    def test_successful_kawarp_check_starts_artwork_worker(self):
        from collections import deque
        import threading
        import visualizer_artwork as artwork

        updates = deque(maxlen=1)
        stop = threading.Event()
        called = threading.Event()
        settings = Obj(palette="auto", artwork="cover.jpg")
        with (patch.object(artwork, "node_binary", return_value="node"),
              patch.object(artwork, "kawarp_palette", return_value=FALLBACK) as renderer,
              patch.object(artwork, "watch_art", side_effect=lambda *args: called.set()) as watch):
            artwork.start_artwork(settings, updates, stop)
            self.assertTrue(called.wait(timeout=2))
            watch.assert_called_once()
            node, source, actual_updates, actual_stop = watch.call_args.args
            self.assertEqual(node, "node")
            self.assertTrue(callable(source))
            self.assertIs(actual_updates, updates)
            self.assertIs(actual_stop, stop)
            renderer.assert_called_once()


class AudioTests(unittest.TestCase):
    def test_name_match_and_explicit_audio_index(self):
        sources = [(1, "Speakers"), (2, "USB Speakers")]
        self.assertEqual(select_input(sources, Obj(audio_device_name="speakers", audio_device_idx=None),
                                      lambda: None), sources[0])
        self.assertEqual(select_input(sources, Obj(audio_device_name="speakers", audio_device_idx=2),
                                      lambda: None), sources[1])

    def test_windows_selects_native_wasapi_loopback_format(self):
        speakers = {"index": 2, "name": "Speakers [Loopback]", "defaultSampleRate": 48000,
                    "maxInputChannels": 6, "isLoopbackDevice": True}
        audio = Mock()
        audio.get_device_count.return_value = 1
        audio.get_device_info_by_index.return_value = speakers
        audio.get_default_wasapi_loopback.return_value = speakers
        settings = Settings(Path("unused-settings.txt"), {"audio": "mono"}).effective()
        package = Obj(PyAudio=lambda: audio, paInt16=8, paContinue=0)
        with (patch("visualizer_audio.sys.platform", "win32"),
              patch.dict(sys.modules, {"pyaudiowpatch": package}), contextlib.ExitStack() as cleanup):
            source = open_audio(settings, False, cleanup)
            callback = audio.open.call_args.kwargs["stream_callback"]
            callback(bytes(BLOCK * 6), BLOCK // 2, None, None)
            callback(bytes(BLOCK * 6), BLOCK // 2, None, None)
            self.assertEqual((source.channels, len(source.frames[0])), (6, BLOCK * 6 * 2))
            self.assertEqual(audio.open.call_args.kwargs["input_device_index"], 2)
        audio.open.return_value.close.assert_called_once()
        audio.terminate.assert_called_once()

    def test_linux_defaults_to_sink_monitor(self):
        settings = Settings(Path("unused-settings.txt")).effective()
        process = Mock()
        process.stdout.read.return_value = b""
        process.poll.return_value = 0
        commands = [Obj(stdout="1\tinput\tstuff\n3\toutput.monitor\tstuff\n"), Obj(stdout="output\n")]
        with (patch("visualizer_audio.sys.platform", "linux"),
              patch("visualizer_audio.subprocess.run", side_effect=commands),
              patch("visualizer_audio.shutil.which", return_value="/usr/bin/parec"),
              patch("visualizer_audio.subprocess.Popen", return_value=process) as popen,
              contextlib.ExitStack() as cleanup):
            source = open_audio(settings, False, cleanup)
            self.assertEqual((source.name, source.rate, source.channels), ("output.monitor", RATE, 2))
            self.assertIn("--device=output.monitor", popen.call_args.args[0])
        process.stdout.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
