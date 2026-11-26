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
export TLYRICS_SOURCE="$SOURCE"
"$PYTHON" - <<'PY'
import os
from pathlib import Path
import shlex
import shutil
import sys

source = Path(os.environ['TLYRICS_SOURCE'])
target = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'tlyrics'
binary = Path.home() / '.local/bin/tlyrics'
marker = '# tlyrics launcher'
if binary.exists() or binary.is_symlink():
    if binary.is_symlink() or marker not in binary.read_text():
        raise SystemExit('An unrelated ~/.local/bin/tlyrics exists; move it before installing.')
target.mkdir(parents=True, exist_ok=True)
import subprocess
python = sys.executable
try:
    import yaml
except ImportError:
    environment = target / '.venv'
    try:
        subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(environment)], check=True)
        python = str(environment / 'bin/python')
        subprocess.run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r',
                        str(source / 'requirements.txt')], check=True)
    except subprocess.CalledProcessError:
        raise SystemExit('Could not install PyYAML. Install python-yaml (Arch), python3-yaml (Debian/Fedora),\n'
                         'or enable Python venv/pip support and rerun install.sh.')
for name in ('tlyrics.py', 'settings.py', 'browser_bridge.py', 'browser.user.js',
             'lyrics.py', 'timing.py', 'rendering.py', 'requirements.txt', 'LICENSE'):
    src, dst = source / name, target / name
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
binary.parent.mkdir(parents=True, exist_ok=True)
temporary = binary.with_suffix('.tmp')
temporary.write_text('#!/bin/sh\n' + marker + '\nexec ' + shlex.quote(python) + ' ' +
                     shlex.quote(str(target / 'tlyrics.py')) + ' "$@"\n')
temporary.chmod(0o755)
temporary.replace(binary)
print('Installed. Run: tlyrics')
if str(binary.parent) not in os.environ.get('PATH', '').split(os.pathsep):
    print('Add ~/.local/bin to your shell PATH, then open a new terminal.')
PY
