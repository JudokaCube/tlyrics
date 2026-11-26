"""Persistent preferences and safe command aliases."""
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'tlyrics' / 'settings.json'
BIN = Path.home() / '.local/bin'
DEFAULTS = dict(player='auto', theme='cyan', layout='scroll', align='center',
                progress=True, timestamps=False, spacing=1, offset=0.0,
                interval=0.2, cache=True, aliases=[], bridge=False)
CHOICES = dict(theme=['cyan', 'green', 'purple', 'amber', 'white'],
               layout=['scroll', 'focus', 'compact'], align=['center', 'left'],
               progress=[True, False], timestamps=[False, True], spacing=[1, 2],
               interval=[0.1, 0.2, 0.5, 1.0], cache=[True, False], bridge=[False, True])


def load():
    result = DEFAULTS.copy()
    try:
        data = json.loads(CONFIG.read_text())
        if not isinstance(data, dict):
            return result
        for key, value in data.items():
            if key in CHOICES and value in CHOICES[key] and type(value) is type(DEFAULTS[key]):
                result[key] = value
            elif key == 'player' and isinstance(value, str) and len(value) < 200:
                result[key] = value
            elif key == 'offset' and type(value) in (int, float) and math.isfinite(value) and abs(value) <= 60:
                result[key] = value
            elif key == 'aliases' and isinstance(value, list):
                result[key] = [s for s in value if isinstance(s, str) and re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,31}', s)]
    except (OSError, ValueError):
        pass
    return result


def save(config):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temp = CONFIG.with_suffix(f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(config, indent=2) + '\n')
    temp.replace(CONFIG)


def alias_content():
    return '#!/bin/sh\n# tlyrics managed alias\nexec ' + shlex.quote(str(BIN / 'tlyrics')) + ' "$@"\n'


def add_alias(name):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,31}', name) or name == 'tlyrics':
        raise ValueError('Use 1–32 letters, digits, underscores or hyphens; start with a letter.')
    path = BIN / name
    if path.exists() or path.is_symlink() or shutil.which(name):
        raise ValueError('That command already exists. Choose another name.')
    BIN.mkdir(parents=True, exist_ok=True)
    with path.open('x') as handle:
        handle.write(alias_content())
    path.chmod(0o755)


def remove_alias(name):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,31}', name):
        raise ValueError('Invalid alias name.')
    path = BIN / name
    if not path.exists():
        return
    if path.is_symlink() or path.read_text() != alias_content():
        raise ValueError('This command was modified; it will not be removed.')
    path.unlink()
