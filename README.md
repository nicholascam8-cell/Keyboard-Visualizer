# Keyboard Visualizer

Turn system audio from Spotify, games, browsers, or other apps into RGB lighting. Use the original bass, mid, and treble effects with Kawarp artwork colours, or switch to CalcProgrammer1's spectrum layout with frequency bars. Both styles can run with a desktop preview.

Hardware control works through OpenRGB. A Direct per-LED mode gives you live animations. A supported fixed-colour mode gives you a solid palette colour. A device needs a lighting controller that OpenRGB can access on the computer running its SDK server. Keyboards without controllable RGB, and keyboards missing from OpenRGB, can still use the on-screen preview. This project controls lighting. [OpenRGB supported devices](https://openrgb.org/devices.html)

## Download and install

On the [cross-platform branch page](https://github.com/nicholascam8-cell/Keyboard-Visualizer/tree/cross-platform-visualizer), confirm that the branch selector says **cross-platform-visualizer**, then choose **Code → Download ZIP**, extract the entire folder, and open a terminal in it. If you use Git, run:

```sh
git clone --branch cross-platform-visualizer https://github.com/nicholascam8-cell/Keyboard-Visualizer.git
cd Keyboard-Visualizer
```

For an existing Git clone, open a terminal in that clone and run:

```sh
git fetch origin &&
git switch cross-platform-visualizer &&
git pull --ff-only
```

If Git reports local changes that would be overwritten, save or commit them before switching. Run `git branch --show-current` to confirm the branch and check that `requirements.txt` is beside `spotify_rgb.py`.

If installation reports `Could not open requirements file`, check the folder and branch using the steps above, then repeat your platform's dependency installation. A later `No module named 'numpy'` error means the selected Python environment still needs the dependencies installed.

Install Python 3.11 or newer. Python 3.13 is a good starting version for the commands below. Each example uses the virtual environment's Python directly, so shell activation is optional. [Python downloads](https://www.python.org/downloads/)

### Windows 10/11

Install Python from python.org with **Tcl/Tk and IDLE** if you want the preview. In PowerShell, run:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe spotify_rgb.py --demo --mode spectrum --palette off
```

Normal runs use WASAPI loopback on the default speakers or headphones. If your player uses another output, run `--list-audio-devices` and select its **[playback loopback]** index with `--audio-device INDEX`. If pip reports that a WASAPI dependency has no wheel for your Python architecture, use the x64 Python installer on Windows 11 ARM and recreate the virtual environment. [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch)

### macOS on Apple Silicon or Intel

Install the matching Python and OpenRGB builds. Install PortAudio for PyAudio. These commands assume Homebrew and its command-line tools are installed:

```sh
brew install portaudio &&
python3.13 -m venv .venv &&
CPPFLAGS="-I$(brew --prefix portaudio)/include" LDFLAGS="-L$(brew --prefix portaudio)/lib" \
  .venv/bin/python -m pip install -r requirements.txt &&
.venv/bin/python spotify_rgb.py --demo --mode spectrum --palette off
```

For playback capture, install **BlackHole 2ch**. In **Audio MIDI Setup**, create a **Multi-Output Device** containing your headphones or speakers and BlackHole 2ch. Select the device as the macOS sound output. Grant your terminal microphone access if macOS asks. The visualizer selects BlackHole 2ch by default. [BlackHole multi-output setup](https://github.com/ExistentialAudio/BlackHole/wiki/Multi-Output-Device)

### Linux

Install Python 3.11 or newer, your distribution's virtual environment package, and the PulseAudio command-line utilities for either PipeWire or PulseAudio. Install Tk for the optional desktop window. On Debian and Ubuntu, run:

```sh
sudo apt install python3 python3-venv python3-tk pulseaudio-utils
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python spotify_rgb.py --demo --mode spectrum --palette off
```

On Fedora the equivalent packages include `python3`, `python3-tkinter`, and `pulseaudio-utils`. The default capture source is the monitor of your default sink. Select another `.monitor` source if playback is routed elsewhere. For an ALSA or other PortAudio input, install PortAudio's development headers and compiler packages, run `.venv/bin/python -m pip install PyAudio`, and add `--audio-backend portaudio` to your commands. [PulseAudio monitor sources](https://wiki.freedesktop.org/Software/PulseAudio/Documentation/Developer/AccessControl/)

### Enable keyboard lighting

1. Install and open [OpenRGB](https://openrgb.org/releases.html) for your OS and CPU. On Linux, follow your package's USB permissions and udev setup.
2. Check that the keyboard appears in OpenRGB's device list. If another RGB app takes control, close it before testing.
3. Open **SDK Server** in OpenRGB and start the server on port **6742**. Keep OpenRGB running.
4. From the project folder run the commands below. On Windows, replace `python` with `.\.venv\Scripts\python.exe`. On macOS and Linux, use `.venv/bin/python`.

```sh
python spotify_rgb.py --list-rgb-devices
python spotify_rgb.py --list-audio-devices
python spotify_rgb.py
```

Play audio and press **Ctrl+C** to stop. By default the app selects every supported keyboard and retains the existing G203 mouse and microphone name filters. Use `--keyboard-name Apex` to filter keyboards, `--device 2,Apex` to select OpenRGB IDs or name fragments explicitly, or `--all-devices` to include compatible non-keyboard zones. Use `--mouse-name "" --mic-name ""` to turn off the legacy peripheral filters. For an OpenRGB SDK server running on another computer, add `--rgb-host HOST --rgb-port 6742`. Capture runs on the computer launching the visualizer, and the SDK computer controls the lights.

The app runs on Windows, macOS, and desktop Linux. On another device, play audio into a capture input on one of those computers. To view the effects without compatible RGB hardware, use `--preview-only` for your audio or `--demo` for generated audio.

## Enable Kawarp artwork colours

Install [Node.js](https://nodejs.org/en/download) and run `npm ci` from the project folder. Kawarp 1.4.0 turns artwork into the three swatches used by the original effects.

- **macOS** reads cover artwork from the Spotify desktop app. Allow your terminal to control Spotify if macOS asks.
- **Windows** can read cover thumbnails from media apps that publish a Windows media session. In the same Python environment, run `python -m pip install winrt-Windows.Media.Control winrt-Windows.Storage.Streams`. [Windows media API package](https://pypi.org/project/winrt-Windows.Media.Control/)
- **Linux** reads covers from playing MPRIS apps when `playerctl` is installed. [Playerctl](https://github.com/altdesktop/playerctl)

On all three systems, use `--artwork "path/to/cover.jpg"` to choose an image. The built-in palette is available automatically when artwork or Kawarp is unavailable. Use `--palette off` to select it explicitly. Spectrum mode uses Kawarp swatches for both its bars and background by default. The background uses 15% brightness so the bars stand out. Existing profiles keep their saved pattern choices. To apply the album colours to an existing profile, run these commands in the activated project environment, then restart with artwork enabled:

```sh
python spotify_rgb.py set frgd_mode 21
python spotify_rgb.py set bkgd_mode 21
python spotify_rgb.py set bkgd_bright 15
python spotify_rgb.py --mode spectrum --palette auto
```

## Choose a display

```sh
python spotify_rgb.py --mode spectrum --preview
python spotify_rgb.py --preview-only mode=spectrum bkgd_mode=0 frgd_mode=9
python spotify_rgb.py --demo mode=random colours=dynamic artwork=cover.jpg
```

| Effect | What appears |
|---|---|
| `bottom-up` | The original three meters rise from the bottom. This is the default. |
| `top-down` | The meters fill from the top. |
| `horizontal` | Three frequency bands fill from left to right. |
| `central` | Light spreads outward from the centre. |
| `whole` | The keyboard pulses with the strongest band. |
| `random` | Waves, lines, ripples, and bursts respond to sound. |
| `spectrum` | The CalcProgrammer layout uses vertical frequency bars for keyboard matrices, a bass bar for linear strips, and a bass-reactive colour for single zones. |

The original effects support `colours=static` for fixed palette regions and `colours=dynamic` for a moving gradient. Keyboards that report a matrix use their own layout. When a full-size Apex 7 lacks a matrix, the existing Apex profile supplies one.

## Change settings from the terminal

Open a second terminal in the same project folder. Activate the virtual environment in that terminal before using the `python` commands below.

On macOS and Linux:

```sh
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.\\.venv\\Scripts\\Activate.ps1
```

Alternatively, use `.venv/bin/python` on macOS and Linux or `.\\.venv\\Scripts\\python.exe` on Windows in place of `python`. The explicit paths also work when activation is unavailable.

The following commands save changes to `settings.txt` and apply rendering changes to a running session within about 0.35 seconds:

```sh
python spotify_rgb.py mode random
python spotify_rgb.py colours dynamic
python spotify_rgb.py fps 120
python spotify_rgb.py mode spectrum
python spotify_rgb.py set amplitude 150
python spotify_rgb.py set bkgd_bright 15
python spotify_rgb.py status
```

Copy [settings.example.txt](settings.example.txt) to `settings.txt` to start a profile, or put an existing CalcProgrammer `settings.txt` beside `spotify_rgb.py` and launch with `--mode spectrum`. Use `--settings "path/to/show.txt"` in both terminals to adjust a different profile. You can also set `KEYBOARD_VISUALIZER_SETTINGS` for a shortcut.

Launch accepts both CalcProgrammer's `key=value` arguments and flags such as `--amplitude 150`. At launch, flags take priority over `key=value` arguments and the file. When you edit a rendering option, its saved value takes effect live. Restart for audio-source, artwork-source, preview, or OpenRGB connection and device changes. `status` shows the saved profile and the defaults it uses. Older `.visualizer-settings` values for mode, colours, and FPS still load when the active file omits them.

| Key | Values and behaviour |
|---|---|
| `brightness`, `gain` | Overall brightness from 0 to 1, and audio gain above 0 through 10. |
| `delay`, `fps` | Update interval in milliseconds, default 25. `fps=1` through `480` overrides it. `fps=auto` uses the interval. |
| `amplitude`, `decay` | Spectrum sensitivity and peak retention in percent. Defaults 100 and 80. |
| `avg_size`, `avg_mode` | Averaging width from 1 to 128. Mode 0 uses bars, and mode 1 uses a sliding average. |
| `nrml_ofst`, `nrml_scl` | Weights for the low end and for higher frequencies. Defaults 0.04 and 0.5. |
| `fltr_const`, `window_mode` | Spectrum smoothing from 0 to 1. FFT window IDs 0 flat, 1 Hann, 2 Hamming, 3 Blackman. |
| `bkgd_bright`, `bkgd_mode`, `frgd_mode` | Background brightness percent, background pattern ID, and foreground pattern ID. |
| `anim_speed` | Pattern animation speed in percent. Negative values reverse direction. |
| `single_color_mode` | IDs 0–8 select solid colours from the pattern list, 9 uses the background, 10 follows it with bass, and 11 follows the foreground with bass. |
| `reactive_bkgd`, `silent_bkgd`, `background_timeout` | Select bass-reactive or silence-only background with 0/1 flags. The timeout uses units of 10 ms. A value of 0 disables the idle fade. |
| `audio_device_idx` | Input index from `--list-audio-devices` on the current machine, or `auto`. Audio IDs can change across platforms and devices. |
| `startminimized` | Starts the optional preview minimized. The terminal stays available. |

Pattern IDs follow the current upstream source. **0–8** are black, white, red, orange, yellow, green, cyan, blue, and purple. **9–14** are green/yellow/red, green/white/red, blue/cyan/white, red/white/blue, rainbow bars, and reverse rainbow bars. **15–20** are original rainbow, HSV rainbow, colour wheel, colour wheel 2, spectrum cycle, and sinusoidal cycle. **21** is Kawarp. The older upstream `server` and `client` settings for visualizer-to-visualizer synchronization are unsupported. Use the OpenRGB host and port options for a remote lighting server.

Run `python spotify_rgb.py --help` for every startup option.

## Update an existing install

Download and extract the full repository again, or run `git pull` inside your clone. Keep your local `settings.txt` or `.visualizer-settings` and rerun the dependency commands if needed. The older updater that fetched one Python file does not install the modules in this release. If you use the original macOS Shortcut, keep launching `spotify_rgb.py` with all the project files beside it, and point the Shortcut at the Python environment you installed above.

## License

This project is distributed under GPL version 2. See [LICENSE](LICENSE) and [third-party credits and source](THIRD_PARTY.md). Run the development checks with `python -m unittest discover -s tests -v`.
