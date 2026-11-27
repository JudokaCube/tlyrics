"""Install a release beside the current one, then atomically switch launchers."""
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

RUNTIME_FILES = ('tlyrics.py', 'settings.py', 'browser_bridge.py', 'browser.user.js',
                 'lyrics.py', 'timing.py', 'rendering.py', 'updates.py', 'deployment.py',
                 'visualizer.py', 'diagnostics.py', 'players.py', 'requirements.txt', 'VERSION', 'LICENSE')
MARKER = 'tlyrics-install-v1'


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Invalid version; expected major.minor.patch')
    return tuple(map(int, value.split('.')))


def install_root():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'tlyrics'


def manifest(source):
    version = (source / 'VERSION').read_text().strip()
    version_tuple(version)
    if (source / 'release.json').exists():
        data = json.loads((source / 'release.json').read_text())
        if not isinstance(data, dict):
            raise ValueError('Invalid release manifest')
        files = data.get('files')
        if data.get('version') != version or not isinstance(files, list) or not files or len(files) > 100:
            raise ValueError('Invalid release manifest')
        for name in files:
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,80}', name):
                raise ValueError('Invalid release filename')
        if len(set(files)) != len(files):
            raise ValueError('Duplicate release files')
        if not set(RUNTIME_FILES).issubset(files):
            raise ValueError('Release is missing required app files')
    else:
        files = list(RUNTIME_FILES)
    for name in files:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Release file is missing or unsafe: {name}')
    return version, files


def installed_root(module_file=__file__):
    location = Path(module_file).resolve().parent
    if location.parent.name != 'releases':
        return None
    root = location.parent.parent
    try:
        marker = json.loads((root / 'installation.json').read_text())
        if isinstance(marker, dict) and marker.get('format') == MARKER and location.is_relative_to(root / 'releases'):
            return root
    except (OSError, ValueError):
        pass
    return None


def run(command, **kwargs):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=180, **kwargs)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or '')[-1500:].strip()
        raise RuntimeError(detail or 'Installation check failed') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Installation timed out; the current version was kept') from exc


def install(source, root=None, binary=None, update=False):
    source = Path(source).resolve()
    root = (Path(root) if root is not None else install_root()).resolve()
    binary = Path(binary) if binary is not None else Path.home() / '.local/bin/tlyrics'
    version, files = manifest(source)
    if binary.exists() or binary.is_symlink():
        if binary.is_symlink() or '# tlyrics launcher' not in binary.read_text():
            raise ValueError('An unrelated tlyrics command already exists; it was not changed')
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another tlyrics installation is already running') from None
        current = root / 'current'
        old = current.resolve() if current.is_symlink() else None
        if old and not old.is_relative_to(root.resolve() / 'releases'):
            raise ValueError('The current release points outside this installation')
        if current.exists() and not current.is_symlink():
            raise ValueError('The installation current path is not a managed link')
        if update and old:
            present = (old / 'VERSION').read_text().strip()
            if version_tuple(version) <= version_tuple(present):
                raise ValueError('This version is already installed, or is older')
        releases = root / 'releases'
        releases.mkdir(exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix=version + '-', dir=releases))
        link = root / ('.current-' + target.name)
        switched = False
        try:
            for name in files:
                shutil.copy2(source / name, target / name)
            (target / 'release.json').write_text(json.dumps({'version': version, 'files': files}) + '\n')
            for path in target.glob('*.py'):
                compile(path.read_bytes(), str(path), 'exec')
            python = sys.executable
            same_requirements = old is not None and (old / 'requirements.txt').read_bytes() == (target / 'requirements.txt').read_bytes()
            if not (update and same_requirements):
                # Each new dependency environment belongs to its release, preserving rollback.
                environment = target / '.venv'
                run([sys.executable, '-m', 'venv', '--system-site-packages', str(environment)])
                python = str(environment / 'bin/python')
                run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(target / 'requirements.txt')])
            run([python, '-c', 'import curses, dbus, yaml, numpy'])
            result = run([python, str(target / 'tlyrics.py'), '--version'])
            if result.stdout.strip() != f'tlyrics {version}':
                raise ValueError('Installed version did not match the release')
            launch = target / 'launch'
            launch.write_text('#!/bin/sh\nexec ' + shlex.quote(python) + ' ' + shlex.quote(str(target / 'tlyrics.py')) + ' "$@"\n')
            launch.chmod(0o755)
            binary.parent.mkdir(parents=True, exist_ok=True)
            launcher_text = '#!/bin/sh\n# tlyrics launcher\nexec ' + shlex.quote(str(root / 'current/launch')) + ' "$@"\n'
            # Prepare the launcher before activation, so permission errors cannot leave a half-install.
            temporary = binary.with_name('.tlyrics-' + target.name)
            try:
                temporary.write_text(launcher_text)
                temporary.chmod(0o755)
                info = root / ('.installation-' + target.name)
                info.write_text(json.dumps({'format': MARKER}) + '\n')
                info.replace(root / 'installation.json')
                link.symlink_to(target.relative_to(root))
                link.replace(current)
                switched = True
                temporary.replace(binary)
            except Exception:
                if switched:
                    if old:
                        link.symlink_to(old)
                        link.replace(current)
                    else:
                        current.unlink(missing_ok=True)
                    switched = False
                raise
            finally:
                temporary.unlink(missing_ok=True)
            return root / 'current/launch'
        finally:
            link.unlink(missing_ok=True)
            if not switched:
                shutil.rmtree(target)


if __name__ == '__main__':
    try:
        install(Path(__file__).resolve().parent)
        print('Installed. Run: tlyrics')
        if str(Path.home() / '.local/bin') not in os.environ.get('PATH', '').split(os.pathsep):
            print('Add ~/.local/bin to your shell PATH, then open a new terminal.')
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'tlyrics install: {exc}', file=sys.stderr)
        sys.exit(1)
