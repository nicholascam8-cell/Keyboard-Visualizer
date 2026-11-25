# SPDX-License-Identifier: GPL-2.0-only
"""Select and capture system audio or an explicit input."""
from collections import deque
from dataclasses import dataclass
import shutil
import subprocess
import sys
import threading

RATE = 48000
BLOCK = 1024


@dataclass
class AudioCapture:
    name: str
    rate: int
    channels: int
    frames: deque
    process: subprocess.Popen | None = None


def linux_monitors():
    try:
        result = subprocess.run(["pactl", "list", "short", "sources"], check=True,
                                text=True, capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("PulseAudio/PipeWire capture needs pactl and a running audio session. "
                           "Use --audio-backend portaudio for another input.") from exc
    return [(int(index), name) for line in result.stdout.splitlines() if line.strip()
            for index, name, *_ in [line.split()]]


def select_input(devices, settings, default):
    index, name = settings.audio_device_idx, settings.audio_device_name
    if index is not None:
        matches = [item for item in devices if item[0] == index]
    elif name:
        exact = [item for item in devices if name.casefold() == item[1].casefold()]
        matches = exact or [item for item in devices if name.casefold() in item[1].casefold()]
    else:
        return default()
    if len(matches) != 1:
        raise RuntimeError("Audio input missing or ambiguous. Use --list-audio-devices "
                           "and select a source with --audio-device INDEX.")
    return matches[0]


def pulse_input(settings, list_only, cleanup):
    sources = linux_monitors()
    if list_only:
        for index, name in sources:
            print(f"{index}  {name}")
        return None

    def default_monitor():
        try:
            result = subprocess.run(["pactl", "get-default-sink"], check=True, text=True,
                                    capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("Could not find the default playback output. "
                               "Use --list-audio-devices to select a monitor.") from exc
        name = result.stdout.strip() + ".monitor"
        for item in sources:
            if item[1] == name:
                return item
        raise RuntimeError(f"No source named {name}. Use --list-audio-devices to select a source.")

    _, name = select_input(sources, settings, default_monitor)
    if not shutil.which("parec"):
        raise RuntimeError("Install parec using your distribution's PulseAudio utilities package.")
    channels = 1 if settings.audio == "mono" else 2
    process = subprocess.Popen(
        ["parec", "--raw", f"--device={name}", "--format=s16le", f"--rate={RATE}",
         f"--channels={channels}", "--latency-msec=30"],
        stdout=subprocess.PIPE,
    )
    frames = deque(maxlen=6)

    def collect():
        frame_size = BLOCK * channels * 2
        while (chunk := process.stdout.read(frame_size)) and len(chunk) == frame_size:
            frames.append(chunk)

    reader = threading.Thread(target=collect, name="PulseAudio capture", daemon=True)
    reader.start()

    def close():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reader.join()
        process.stdout.close()

    cleanup.callback(close)
    return AudioCapture(name, RATE, channels, frames, process)


def portaudio_input(settings, list_only, cleanup):
    try:
        if sys.platform == "win32":
            import pyaudiowpatch as pyaudio
        else:
            import pyaudio
    except ImportError as exc:
        raise RuntimeError("Install the audio dependencies with python -m pip install -r requirements.txt.") from exc

    audio = pyaudio.PyAudio()
    cleanup.callback(audio.terminate)
    inputs = [audio.get_device_info_by_index(i) for i in range(audio.get_device_count())]
    inputs = {dev["index"]: dev for dev in inputs if dev["maxInputChannels"] > 0}
    devices = [(idx, dev["name"]) for idx, dev in inputs.items()]
    if list_only:
        if not devices:
            print("No audio inputs found.")
        for index, name in devices:
            suffix = " [playback loopback]" if inputs[index].get("isLoopbackDevice") else ""
            print(f"{index}  {name}{suffix}")
        return None

    def default_device():
        if sys.platform == "win32":
            try:
                info = audio.get_default_wasapi_loopback()
            except (OSError, LookupError) as exc:
                raise RuntimeError("Could not open default WASAPI loopback. "
                                   "Use --list-audio-devices to select an input.") from exc
            return info["index"], info["name"]
        if sys.platform == "darwin":
            for item in devices:
                if item[1].casefold() == "blackhole 2ch":
                    return item
            raise RuntimeError("BlackHole 2ch is missing. Install it for system audio or "
                               "select an input with --list-audio-devices and --audio-device INDEX.")
        info = audio.get_default_input_device()
        return info["index"], info["name"]

    index, name = select_input(devices, settings, default_device)
    dev = inputs[index]
    rate = round(dev["defaultSampleRate"])
    channels = int(dev["maxInputChannels"])
    # WASAPI loopback may require the playback endpoint's native channel count.
    if not dev.get("isLoopbackDevice"):
        channels = min(1 if settings.audio == "mono" else 2, channels)
    frames = deque(maxlen=6)
    pending = bytearray()
    frame_size = BLOCK * channels * 2

    def capture(data, count, timing, status):
        pending.extend(data)
        while len(pending) >= frame_size:
            frames.append(bytes(pending[:frame_size]))
            del pending[:frame_size]
        return (None, pyaudio.paContinue)

    try:
        stream = audio.open(format=pyaudio.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=index, frames_per_buffer=BLOCK, stream_callback=capture)
    except OSError as exc:
        raise RuntimeError(f"Could not capture {name}: {exc}. Try another listed audio source.") from exc
    cleanup.callback(stream.close)
    return AudioCapture(name, rate, channels, frames)


def open_audio(settings, list_only, cleanup):
    backend = settings.audio_backend
    if backend == "auto":
        backend = "pulse" if sys.platform.startswith("linux") else "portaudio"
    return (pulse_input if backend == "pulse" else portaudio_input)(settings, list_only, cleanup)
