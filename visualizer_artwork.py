# SPDX-License-Identifier: GPL-2.0-only
"""Render original Kawarp palettes from the current media artwork."""
import asyncio
from contextlib import closing
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlparse
from urllib.request import url2pathname

import numpy as np

from visualizer_settings import ROOT

FALLBACK = np.array([[60, 120, 230], [180, 75, 165], [65, 190, 140]], dtype=float)
MAX_ARTWORK_BYTES = 16 * 1024 * 1024

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


def local_artwork(path):
    path = Path(path).expanduser()
    stat = path.stat()
    if stat.st_size > MAX_ARTWORK_BYTES:
        raise ValueError("cover exceeds 16 MB")
    return path.name, (path, stat.st_mtime_ns, stat.st_size), path.read_bytes


def url_artwork(song, url):
    if urlparse(url).scheme == "file":
        _, key, read = local_artwork(url2pathname(urlparse(url).path))
        return song, key, read
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("the player published an unsupported artwork URL")
    return song, url, lambda: download_artwork(url)


def download_artwork(url):
    import requests

    with requests.get(url, timeout=(3, 6), stream=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            data.extend(chunk)
            if len(data) > MAX_ARTWORK_BYTES:
                raise ValueError("cover exceeds 16 MB")
    return data


def mac_artwork():
    result = subprocess.run(["/usr/bin/osascript", "-e", SPOTIFY], capture_output=True,
                            text=True, check=True, timeout=5)
    song, sep, url = result.stdout.strip().rpartition("\n")
    if sep and url:
        return url_artwork(song, url)


def linux_artwork():
    def playerctl(*args):
        return subprocess.run(["playerctl", *args], capture_output=True, text=True,
                              timeout=3).stdout.strip()

    for player in playerctl("--list-all").splitlines():
        if playerctl("--player", player, "status") != "Playing":
            continue
        song, sep, url = playerctl("--player", player, "metadata", "--format",
                                   "{{title}}\n{{mpris:artUrl}}").rpartition("\n")
        if sep and url:
            return url_artwork(song, url)


async def windows_artwork(last_key):
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winrt.windows.storage.streams import DataReader

    manager = await SessionManager.request_async()
    current = manager.get_current_session()
    sessions = [current, *(session for session in manager.get_sessions() or () if session != current)]
    for session in sessions:
        if session is None:
            continue
        playback = session.get_playback_info()
        if not playback or playback.playback_status != PlaybackStatus.PLAYING:
            continue
        media = await session.try_get_media_properties_async()
        if media is None or media.thumbnail is None:
            continue
        key = (session.source_app_user_model_id, media.title, media.artist, media.album_title)
        if last_key == key:
            return None
        with closing(await media.thumbnail.open_read_async()) as stream:
            if stream.size > MAX_ARTWORK_BYTES:
                raise ValueError("cover exceeds 16 MB")
            with closing(DataReader(stream.get_input_stream_at(0))) as reader:
                await reader.load_async(stream.size)
                data = bytes(reader.read_buffer(stream.size))
        return media.title, key, lambda: data


def watch_art(node, source, updates, stop):
    from PIL import Image

    last_key = last_error = None
    while not stop.is_set():
        try:
            item = source(last_key)
            if item is not None:
                song, key, read = item
                if key != last_key:
                    with Image.open(io.BytesIO(read())) as image:
                        rgba = image.convert("RGB").resize((64, 64), Image.Resampling.BOX).convert("RGBA")
                    colors = kawarp_palette(node, rgba.tobytes())
                    updates.append(colors)
                    last_key = key
                    print(f"Kawarp • {song} • " + " ".join("#%02x%02x%02x" % tuple(c.astype(int)) for c in colors),
                          flush=True)
            last_error = None
        except Exception as exc:
            message = str(getattr(exc, "stderr", "") or exc).strip()
            if message != last_error:
                print("Kawarp artwork unavailable:", message, flush=True)
                last_error = message
        stop.wait(2)


def start_artwork(settings, updates, stop):
    if settings.palette == "off":
        return
    if settings.artwork:
        source = lambda _: local_artwork(settings.artwork)
    elif sys.platform == "darwin":
        source = lambda _: mac_artwork()
    elif sys.platform.startswith("linux") and shutil.which("playerctl"):
        source = lambda _: linux_artwork()
    elif sys.platform == "win32":
        try:
            from winrt.windows.media import control
            from winrt.windows.storage import streams
        except ImportError:
            print("Kawarp auto artwork needs the Windows media packages listed in the README. "
                  "Use --artwork FILE to supply a cover.", flush=True)
            return
        source = lambda key: asyncio.run(asyncio.wait_for(windows_artwork(key), timeout=5))
    else:
        print("Kawarp auto artwork is unavailable. Use --artwork FILE to supply a cover.", flush=True)
        return
    try:
        node = node_binary()
        kawarp_palette(node, bytes([64, 128, 192, 255]) * 4096)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Using built-in colours. {exc}", flush=True)
        return
    threading.Thread(target=watch_art, args=(node, source, updates, stop),
                     daemon=True, name="Kawarp artwork").start()
