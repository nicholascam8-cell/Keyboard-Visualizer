#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
# Start the configured visualizer from Finder or macOS Shortcuts.
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
project_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$project_dir"

if [ ! -x .venv/bin/python ]; then
    printf '%s\n' "Python environment missing. Complete the macOS setup in README.md first."
    exit 1
fi

if command -v spicetify >/dev/null 2>&1; then
    spicetify upgrade || printf '%s\n' "Spicetify upgrade failed. Continuing with the visualizer."
fi

/usr/bin/open -a Spotify

if ! /usr/bin/nc -z -G 1 127.0.0.1 6742 >/dev/null 2>&1; then
    /usr/bin/open -a OpenRGB --args --server --startminimized
    ready=0
    for attempt in {1..30}; do
        if /usr/bin/nc -z -G 1 127.0.0.1 6742 >/dev/null 2>&1; then
            ready=1
            break
        fi
        /bin/sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        printf '%s\n' "OpenRGB's SDK server is unavailable." \
            "In OpenRGB, select SDK Server and click Start Server on port 6742, then run this launcher again."
        exit 1
    fi
fi

printf '%s\n' "Starting your saved visualizer settings. Press Control-C to stop."
exec .venv/bin/python spotify_rgb.py "$@"
