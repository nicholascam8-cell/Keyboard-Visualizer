# Keyboard Visualizer

A macOS music visualizer for the SteelSeries Apex 7. BlackHole captures the audio, Kawarp supplies colours from Spotify album artwork, and OpenRGB sends the lighting to the keyboard, G203 mouse, and microphone. The keyboard responds to bass, mids, and treble. Peripheral lighting depends on the modes each device exposes.

## Start and stop

Use your keyboard music Shortcut, or run the installed launcher in Terminal:

```bash
bash "$HOME/Documents/OpenRGBProject/start-keyboard-music.command"
```

The launcher closes existing Spotify, OpenRGB, and visualizer processes before starting the session. Play a song in Spotify. Press **Control-C** in the launcher tab to stop.

## Change settings while music plays

Open a second Terminal tab with **⌘T**. Paste this once per tab to make the short `rgb` commands below available from any folder:

```bash
rgb() { /usr/local/bin/python3 "$HOME/Documents/OpenRGBProject/spotify_rgb.py" "$@"; }
```

Try `rgb mode random`, then `rgb colours dynamic`. The visualizer reads saved changes roughly every 0.35 seconds. They carry over to the next session.

| Command | Result |
|---|---|
| `rgb mode bottom-up` | Bass, mid, and treble meters rise from the bottom. Default mode. |
| `rgb mode top-down` | The meters fill from the top. |
| `rgb mode horizontal` | Three horizontal bands fill from left to right. |
| `rgb mode central` | Light spreads outward from the centre. |
| `rgb mode whole` | The whole keyboard pulses with the strongest frequency band. |
| `rgb mode random` | Alternates between waves, lines, ripples, and bursts during playback. |
| `rgb colours static` | Keeps album colours anchored to keyboard regions. Default colour setting. |
| `rgb colours dynamic` | Moves a smooth album-colour gradient across the keys. More audio energy increases its speed. |
| `rgb fps 120` | Sets the keyboard render cap. Choose any integer from 1 to 480. Default is 40. Actual output depends on the hardware. |
| `rgb status` | Prints the saved mode, colour setting, and FPS cap. |

To return to the defaults:

```bash
rgb mode bottom-up
rgb colours static
rgb fps 40
```

## Check and install updates

These commands use the separately installed **spotify_rgb updater**. If you have the extracted updater kit in Downloads, install it with `bash "$HOME/Downloads/spotify-rgb-tools/install.command"` and open a new Terminal tab.

| Command | What it does |
|---|---|
| `spotify_rgb check` | Compares the visualizer with its release source and reports dependency versions, including Spicetify. |
| `spotify_rgb update` | Installs the visualizer from its configured source. If the file changes, the updater backs up your previous copy. Restart the Shortcut afterward. |
| `spotify_rgb update "$HOME/Downloads/spotify_rgb.py"` | Installs a specific downloaded script. |
| `spotify_rgb set-source URL` | Saves a raw HTTPS Python file as the release source. |
| `spotify_rgb --help` | Lists the updater commands. |

Connect this repository once:

```bash
spotify_rgb set-source "https://raw.githubusercontent.com/nicholascam8-cell/Keyboard-Visualizer/main/spotify_rgb.py"
spotify_rgb check
```

To **publish** a local edit, upload the updated file as `spotify_rgb.py` at the repository root and commit it to `main`. To **download** that edit on another configured Mac, run `spotify_rgb update`. [Upload to this repository](https://github.com/nicholascam8-cell/Keyboard-Visualizer/upload/main)

## Startup options

Pass flags to the launcher to change one run. For example:

```bash
bash "$HOME/Documents/OpenRGBProject/start-keyboard-music.command" --brightness 0.75 --gain 1.3
```

| Flag | Values and behaviour |
|---|---|
| `--brightness` | Overall brightness from 0 to 1. Default 1. |
| `--gain` | Audio sensitivity above 0 and up to 10. Default 1. |
| `--fps`, `--keyboard-fps` | Startup cap from 1 to 480. A saved `rgb fps` setting takes priority. |
| `--audio-device-name` | Input name or unique substring. Default `BlackHole 2ch`. |
| `--audio-device` | Numeric input device index. Overrides the name. |
| `--audio` | `mono` or `stereo`. Default stereo. |
| `--list-audio-devices` | Lists input names and indices, then exits. Run `rgb --list-audio-devices` directly. |
| `--mouse-name` | Mouse name substring. Default `G203`. Pass `--mouse-name ""` to skip. |
| `--mic-name` | Microphone lighting device substring. Default `888-000534`. Pass `--mic-name ""` to skip. |
| `--palette auto` | Selects automatic Kawarp album colours. |
| `-h`, `--help` | Displays startup help. Run `rgb --help`. |

## Setup notes

The examples assume the project lives in `~/Documents/OpenRGBProject` and uses `/usr/local/bin/python3`. The Python environment needs NumPy, PyAudio, openrgb-python, Requests, and Pillow. With Node.js and npm installed, install Kawarp beside the script:

```bash
cd "$HOME/Documents/OpenRGBProject" && npm install @kawarp/core@1.4.0
```

Select a macOS Multi-Output Device containing **BlackHole 2ch** and your speakers or headphones. If the printed bass/mid/high levels stay at zero while music plays, check this routing. For a manual launch with the OpenRGB SDK server already running, use `rgb` by itself.
