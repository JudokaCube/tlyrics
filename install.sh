#!/bin/sh
set -eu

if [ "$(uname -s)" != Linux ]; then
    echo 'tlyrics currently supports Linux desktop sessions.' >&2
    exit 1
fi
if [ "$(id -u)" = 0 ]; then
    echo 'Run this installer as your normal desktop user, without sudo.' >&2
    exit 1
fi
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys, curses, dbus; assert sys.version_info >= (3, 10)' 2>/dev/null; then
    echo 'Install Python 3.10+ and the Python D-Bus bindings first:' >&2
    echo '  Arch:          sudo pacman -S python python-dbus' >&2
    echo '  Debian/Ubuntu: sudo apt install python3 python3-dbus' >&2
    echo '  Fedora:        sudo dnf install python3 python3-dbus' >&2
    exit 1
fi
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$PYTHON" "$SOURCE/deployment.py"
