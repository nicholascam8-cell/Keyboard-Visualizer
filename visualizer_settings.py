# SPDX-License-Identifier: GPL-2.0-only
"""Read KeyboardVisualizer settings.txt and apply live terminal changes."""
import argparse
from contextlib import contextmanager
import math
import os
from pathlib import Path
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
MODES = ("bottom-up", "horizontal", "top-down", "central", "whole", "random",
         "spectrum")
PATTERNS = ("Black", "White", "Red", "Orange", "Yellow", "Green", "Cyan", "Blue",
            "Purple", "Green/Yellow/Red", "Green/White/Red", "Blue/Cyan/White",
            "Red/White/Blue", "Rainbow Bars", "Rainbow Bars Inverse", "Original",
            "Rainbow", "Color Wheel", "Color Wheel 2", "Spectrum Cycle",
            "Sinusoidal Cycle", "Kawarp")
LEGACY = ("mode", "colours", "fps")
TERMINAL_MARKER = "# Terminal update "
ALIASES = {"colors": "colours", "keyboard_fps": "fps",
           "audio_device": "audio_device_idx", "openrgb_host": "rgb_host",
           "openrgb_port": "rgb_port"}
RESTART = {
    "audio_backend", "audio_device_idx", "audio_device_name", "audio", "artwork",
    "palette", "rgb_host", "rgb_port", "rgb", "keyboard_name", "mouse_name",
    "mic_name", "device", "all_devices", "preview", "startminimized", "demo",
}


def number(kind, minimum, maximum=None, *, exclusive_min=False):
    def parse(value):
        val = kind(value)
        if not math.isfinite(val) or val < minimum or (exclusive_min and val == minimum) or (
                maximum is not None and val > maximum):
            lower = "above" if exclusive_min else "at least"
            upper = "" if maximum is None else f" and at most {maximum}"
            raise ValueError(f"must be {lower} {minimum}{upper}")
        return val
    return parse


def choice(*values):
    def parse(value):
        value = value.lower()
        if value not in values:
            raise ValueError("choose " + ", ".join(values))
        return value
    return parse


def boolean(value):
    return choice("0", "1", "false", "true", "off", "on")(value) in ("1", "true", "on")


def or_auto(parse):
    def wrapped(value):
        return None if value.lower() == "auto" else parse(value)
    return wrapped


# Default, value parser, CLI help. Upstream numbers follow VisualizerDefines.h.
SCHEMA = {
    "mode": ("bottom-up", choice(*MODES), "lighting effect: " + ", ".join(MODES)),
    "colours": ("static", choice("static", "dynamic"), "Kawarp colour movement"),
    "fps": (None, or_auto(number(int, 1, 480)), "lighting FPS cap (40 unless delay is set)"),
    "brightness": (1., number(float, 0, 1), "overall brightness from 0 to 1"),
    "gain": (1., number(float, 0, 10, exclusive_min=True), "audio sensitivity multiplier"),
    "amplitude": (100, number(int, 0, 10000), "spectrum sensitivity as a percent"),
    "bkgd_bright": (100, number(int, 0, 100), "spectrum background brightness percent"),
    "avg_size": (8, number(int, 1, 128), "spectrum averaging width"),
    "decay": (80, number(int, 0, 100), "spectrum peak retention percent per analysis frame"),
    "delay": (25, number(int, 0, 10000), "milliseconds between writes when fps is unset"),
    "nrml_ofst": (.04, number(float, 0, 10), "spectrum low-frequency weight"),
    "nrml_scl": (.5, number(float, 0, 10), "additional weight toward high frequencies"),
    "fltr_const": (1., number(float, 0, 1), "spectrum smoothing coefficient"),
    "anim_speed": (100., number(float, -10000, 10000), "spectrum animation speed percent"),
    "window_mode": (1, number(int, 0, 3), "FFT window (0 flat, 1 Hann, 2 Hamming, 3 Blackman)"),
    "bkgd_mode": (15, number(int, 0, len(PATTERNS) - 1), "spectrum background pattern ID"),
    "frgd_mode": (9, number(int, 0, len(PATTERNS) - 1), "spectrum foreground pattern ID"),
    "single_color_mode": (11, number(int, 0, 11), "spectrum single-zone pattern ID"),
    "avg_mode": (0, number(int, 0, 1), "spectrum averaging (0 bars, 1 sliding average)"),
    "reactive_bkgd": (False, boolean, "scale the spectrum background by bass"),
    "silent_bkgd": (False, boolean, "show spectrum background only after silence"),
    "background_timeout": (120, number(int, 0, 360000), "spectrum idle timeout in 10 ms units"),
    "audio_device_idx": (None, or_auto(number(int, 0)), "audio source index from this computer"),
    "audio_device_name": ("", str, "audio source name or unique substring"),
    "audio_backend": ("auto", choice("auto", "portaudio", "pulse"), "audio capture backend"),
    "audio": ("stereo", choice("mono", "stereo"), "audio analysis channels"),
    "palette": ("auto", choice("auto", "off"), "Kawarp artwork palette or built-in colours"),
    "artwork": ("", str, "local cover image for Kawarp"),
    "rgb_host": ("127.0.0.1", str, "OpenRGB SDK server address"),
    "rgb_port": (6742, number(int, 1, 65535), "OpenRGB SDK server port"),
    "rgb": (True, boolean, "send frames to OpenRGB"),
    "keyboard_name": ("", str, "filter automatically selected keyboards by name"),
    "device": ("", str, "select OpenRGB devices by ID or name (comma-separated)"),
    "all_devices": (False, boolean, "select all compatible OpenRGB devices"),
    "mouse_name": ("G203", str, "legacy mouse name filter; empty disables it"),
    "mic_name": ("888-000534", str, "legacy microphone name filter; empty disables it"),
    "preview": (False, boolean, "show a desktop preview"),
    "startminimized": (False, boolean, "open the preview minimized"),
    "demo": (False, boolean, "preview generated audio with no hardware"),
}


def canonical(key):
    key = key.strip().replace("-", "_").lower()
    return ALIASES.get(key, key)


def parse_value(key, value):
    if key not in SCHEMA:
        raise ValueError(f"unknown setting {key!r}")
    try:
        return SCHEMA[key][1](value)
    except ValueError as exc:
        raise ValueError(f"{key}={value!r}: {exc}") from exc


def setting_pair(line):
    line = line.strip()
    if not line or line.startswith(("#", ";", "/")):
        return None
    key, sep, value = line.partition("=")
    key = canonical(key)
    if not sep and key == "startminimized":
        value = "1"
    elif not sep:
        raise ValueError(f"expected key=value, got {line!r}")
    return key, value.strip()


def parse_file(source, path):
    values = {}
    revisions = {}
    ignored = set()
    for index, line in enumerate(source.splitlines(), 1):
        try:
            if line.startswith(TERMINAL_MARKER):
                key, _, revision = line[len(TERMINAL_MARKER):].partition(" ")
                revisions[key] = revision
            pair = setting_pair(line)
            if not pair:
                continue
            key, value = pair
            if key not in SCHEMA:
                ignored.add(key)
                continue
            values[key] = parse_value(key, value)
            if values[key] and key in ("silent_bkgd", "reactive_bkgd"):
                other = "reactive_bkgd" if key == "silent_bkgd" else "silent_bkgd"
                values[other] = False
        except ValueError as exc:
            raise ValueError(f"{path}:{index}: {exc}") from exc
    return values, revisions, ignored


def read_text(path):
    try:
        return path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return ""


@contextmanager
def write_lock(path):
    # The lock stays beside the file so concurrent commands share the same handle.
    with path.with_name(path.name + ".lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def update_file(path, key, value):
    parse_value(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with write_lock(path):
        source = read_text(path)
        lines = []
        for line in source.splitlines():
            if line.startswith(f"{TERMINAL_MARKER}{key} "):
                continue
            try:
                pair = setting_pair(line)
            except ValueError:
                pair = None
            if not pair or pair[0] != key:
                lines.append(line)
        lines.extend((f"{key}={value}", f"{TERMINAL_MARKER}{key} {uuid4().hex}"))
        # Leave a valid existing profile intact if a save fails.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                temp.write("\n".join(lines) + "\n")
                temp.close()
                temp_path.replace(path)
            finally:
                temp_path.unlink(missing_ok=True)


class Settings:
    def __init__(self, path, overrides=None, legacy_dir=None):
        self.path = path
        self.legacy_dir = legacy_dir
        self.overrides = overrides or {}
        self.warned = set()
        self.signature = self.file_signature()
        self.initial, self.revisions = self.read()
        self.saved = self.initial
        self.changed = set()

    def file_signature(self):
        def stamp(path):
            try:
                stat = path.stat()
            except FileNotFoundError:
                return None
            return stat.st_mtime_ns, stat.st_size, stat.st_ino

        legacy = () if self.legacy_dir is None else tuple(stamp(self.legacy_dir / key) for key in LEGACY)
        return stamp(self.path), legacy

    def read(self):
        values, revisions, ignored = parse_file(read_text(self.path), self.path)
        if self.legacy_dir is not None:
            for key in LEGACY:
                if key in values:
                    continue
                try:
                    values[key] = parse_value(key, read_text(self.legacy_dir / key).strip())
                except (OSError, ValueError):
                    pass
        new = ignored - self.warned
        if new:
            print(f"Ignoring unsupported settings in {self.path}: {', '.join(sorted(new))}",
                  flush=True)
            self.warned.update(new)
        return values, revisions

    def reload(self):
        signature = self.file_signature()
        if signature != self.signature:
            values, revisions = self.read()
            pairs = values.items() ^ self.saved.items()
            marked = revisions.items() ^ self.revisions.items()
            self.changed.update(key for key, _ in pairs | marked if key in SCHEMA and key not in RESTART)
            self.saved, self.revisions = values, revisions
            self.signature = signature
        return self.effective()

    def effective(self):
        defaults = {key: spec[0] for key, spec in SCHEMA.items()}
        startup = {key: value for key, value in self.initial.items() if key not in self.changed}
        live = {key: self.saved[key] for key in self.changed if key in self.saved}
        effective = defaults | startup | self.overrides | live
        if live.get("silent_bkgd"):
            effective["reactive_bkgd"] = False
        if live.get("reactive_bkgd"):
            effective["silent_bkgd"] = False
        return argparse.Namespace(**effective)


def output_fps(settings):
    if settings.fps is not None:
        return settings.fps
    return min(480, 1000 / settings.delay) if settings.delay else 480


def command_line(argv=None):
    parser = argparse.ArgumentParser(
        description="Audio-reactive OpenRGB lighting and spectrum preview",
        epilog="Use key=value or --key value at startup. Live commands: mode EFFECT, "
               "colours static|dynamic, fps NUMBER, set KEY VALUE, status.")
    parser.add_argument("--settings", type=Path, help="profile path (default: settings.txt beside script)")
    for key, (_, parse, help_text) in SCHEMA.items():
        flags = ["--" + key.replace("_", "-")]
        flags.extend("--" + alias.replace("_", "-") for alias, target in ALIASES.items() if target == key)
        options = {"action": argparse.BooleanOptionalAction} if parse is boolean else {"type": str, "metavar": "VALUE"}
        parser.add_argument(*flags, dest=key, default=argparse.SUPPRESS, help=help_text, **options)
    parser.add_argument("--preview-only", action="store_true", help="preview without OpenRGB")
    parser.add_argument("--list-audio-devices", action="store_true", help="list capture devices and exit")
    parser.add_argument("--list-rgb-devices", action="store_true", help="list OpenRGB devices and exit")
    parser.add_argument("tokens", nargs="*", metavar="key=value")
    args = parser.parse_intermixed_args(argv)
    if args.tokens == ["help"]:
        parser.print_help()
        return None
    path = args.settings or Path(os.environ.get("KEYBOARD_VISUALIZER_SETTINGS", ROOT / "settings.txt"))
    flag_values = {key: getattr(args, key) for key in SCHEMA if hasattr(args, key)}
    try:
        if args.tokens and args.tokens[0] in (*LEGACY, "colors", "status", "set"):
            command, *params = args.tokens
            if command == "status" and not params:
                settings = Settings(path, legacy_dir=path.parent / ".visualizer-settings")
                print(path.resolve())
                effective = settings.effective()
                for key, value in vars(effective).items():
                    if key == "fps" and value is None:
                        value = output_fps(effective)
                    print(f"{key} {value if value is not None else 'auto'}")
            elif command == "set" and len(params) == 2:
                key, value = canonical(params[0]), params[1]
                update_file(path, key, value)
                print(f"{key} {value}" + (" (restart to apply)" if key in RESTART else ""))
            elif command in (*LEGACY, "colors") and len(params) == 1:
                key = canonical(command)
                update_file(path, key, params[0])
                print(f"{key} {params[0]}")
            else:
                parser.error("use status, set KEY VALUE, or mode/colours/fps VALUE")
            return None
        overrides = {}
        for token in args.tokens:
            key, value = setting_pair(token) or ("", "")
            overrides[key] = parse_value(key, value)
            if overrides[key] and key in ("silent_bkgd", "reactive_bkgd"):
                other = "reactive_bkgd" if key == "silent_bkgd" else "silent_bkgd"
                overrides[other] = False
        overrides.update({key: value if isinstance(value, bool) else parse_value(key, value)
                          for key, value in flag_values.items()})
        if flag_values.get("silent_bkgd") and flag_values.get("reactive_bkgd"):
            raise ValueError("choose either --silent-bkgd or --reactive-bkgd")
        if flag_values.get("silent_bkgd"):
            overrides["reactive_bkgd"] = False
        if flag_values.get("reactive_bkgd"):
            overrides["silent_bkgd"] = False
        if "audio_device_name" in overrides and "audio_device_idx" not in overrides:
            overrides["audio_device_idx"] = None
        if args.preview_only:
            overrides.update(preview=True, rgb=False)
        return Settings(path, overrides, path.parent / ".visualizer-settings"), args
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
