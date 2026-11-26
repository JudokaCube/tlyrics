"""Persistent preferences and safe command aliases."""
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import copy
import hashlib
import fcntl
import tempfile

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'tlyrics' / 'settings.json'
BIN = Path.home() / '.local/bin'
DEFAULTS = dict(player='auto', theme='cyan', layout='scroll', align='center',
                progress=True, timestamps=False, spacing=1, offset=0.0,
                interval=0.1, cache=True, aliases=[], bridge=False,
                word_sync=True, word_style='whole', glow='off', gradient='off', palette='ocean',
                pulse=False, animate_gradient=False, dim_inactive=True, underline_word=False,
                max_width=100, fps=30, offset_step=0.05, song_offsets={},
                update_mode='ask', update_hours=24)
CHOICES = dict(theme=['cyan', 'green', 'purple', 'amber', 'white'],
               layout=['scroll', 'focus', 'compact'], align=['center', 'left'],
               progress=[True, False], timestamps=[False, True], spacing=[1, 2],
               interval=[0.05, 0.1, 0.2, 0.5, 1.0], cache=[True, False], bridge=[False, True],
               word_sync=[True, False], word_style=['whole', 'sweep'], glow=['off', 'soft', 'bright'],
               gradient=['off', 'word', 'line'], palette=['ocean', 'sunset', 'aurora', 'rainbow'],
               pulse=[False, True], animate_gradient=[False, True], dim_inactive=[True, False],
               underline_word=[True, False], max_width=[60, 80, 100, 0], fps=[15, 30, 60],
               offset_step=[0.05, 0.1, 0.2], update_mode=['ask', 'auto', 'off'],
               update_hours=[6, 24, 168])


DEFAULTS.update(background='terminal', border='none', title_style='bold',
                show_header=True, show_status=True, show_hints=True, progress_style='line',
                active_marker='chevron', visualizer=False, viz_layout='bars', viz_position='bottom',
                viz_height=5, viz_width=0, viz_bars=32, viz_channels='stereo', viz_fps=30,
                viz_backend='pulse', viz_source='auto', viz_sensitivity=100, viz_autosens=True,
                viz_noise=65, viz_smoothing='balanced', viz_decay='balanced', viz_peaks=True,
                viz_colour='palette', viz_palette='ocean', viz_gradient='vertical',
                viz_glyph='blocks', viz_gap=1)
CHOICES.update(background=['terminal', 'black', 'midnight', 'charcoal'],
               border=['none', 'line', 'rounded', 'double'], title_style=['bold', 'accent', 'plain'],
               show_header=[True, False], show_status=[True, False], show_hints=[True, False],
               progress_style=['line', 'blocks', 'dots', 'ascii'], active_marker=['chevron', 'dot', 'bar', 'none'],
               visualizer=[False, True], viz_layout=['bars', 'mirror', 'outline', 'waterfall', 'radial', 'levels'],
               viz_position=['bottom', 'top'], viz_height=[3, 5, 7, 10], viz_width=[0, 40, 60, 80, 100],
               viz_bars=[16, 32, 48, 64], viz_channels=['stereo', 'mono'], viz_fps=[15, 30, 60],
               viz_backend=['pulse', 'pipewire'], viz_sensitivity=[50, 100, 150, 200],
               viz_autosens=[True, False], viz_noise=[0, 35, 65, 85],
               viz_smoothing=['fast', 'balanced', 'soft'], viz_decay=['fast', 'balanced', 'slow'],
               viz_peaks=[True, False], viz_colour=['palette', 'theme', 'mono'],
               viz_palette=['ocean', 'sunset', 'aurora', 'rainbow', 'rose', 'forest', 'ice'],
               viz_gradient=['vertical', 'horizontal'], viz_glyph=['blocks', 'thin', 'dots', 'ascii'],
               viz_gap=[0, 1, 2])
CHOICES['theme'] += ['blue', 'red', 'pink', 'teal']
CHOICES['palette'] += ['rose', 'forest', 'ice']
PRESETS = {
    'minimal': dict(theme='white', background='terminal', border='none', gradient='off',
                    glow='off', pulse=False, animate_gradient=False, underline_word=False,
                    show_header=False, show_status=False, show_hints=False, active_marker='none',
                    layout='focus', progress_style='line'),
    'neon': dict(theme='pink', background='midnight', border='rounded', gradient='word',
                 palette='aurora', glow='soft', pulse=False, animate_gradient=True,
                 underline_word=False, show_header=True, show_status=True, show_hints=True,
                 active_marker='bar', layout='scroll', progress_style='blocks'),
    'retro': dict(theme='amber', background='black', border='double', gradient='off',
                  glow='off', pulse=False, animate_gradient=False, underline_word=False,
                  show_header=True, show_status=True, show_hints=True, active_marker='chevron',
                  layout='scroll', progress_style='ascii'),
    'soft': dict(theme='teal', background='charcoal', border='rounded', gradient='word',
                 palette='ice', glow='off', pulse=False, animate_gradient=False,
                 underline_word=False, show_header=True, show_status=True, show_hints=True,
                 active_marker='dot', layout='scroll', progress_style='line'),
}


class Preferences(dict):
    """Remember the loaded values so saves only write locally changed keys."""
    def __init__(self, values, baseline=None):
        super().__init__(values)
        self.baseline = copy.deepcopy(dict(values if baseline is None else baseline))
        self.forced = set()


def clone(config):
    result = Preferences(copy.deepcopy(config), getattr(config, 'baseline', config))
    result.forced = set(getattr(config, 'forced', ()))
    return result


def adopt(config, saved):
    config.clear()
    config.update(copy.deepcopy(saved))
    if isinstance(config, Preferences):
        config.baseline = copy.deepcopy(dict(saved))
        config.forced = set()


def normalise(data, strict=False):
    result = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        raise ValueError('Settings must be a JSON object.')
    for key, value in data.items():
        valid = False
        if key in CHOICES:
            valid = value in CHOICES[key] and (type(value) is type(DEFAULTS[key]) or
                    type(DEFAULTS[key]) is float and type(value) in (int, float))
            if valid and type(DEFAULTS[key]) is float:
                value = float(value)
        elif key == 'player':
            valid = isinstance(value, str) and 0 < len(value) < 200 and value.isprintable()
        elif key == 'viz_source':
            valid = isinstance(value, str) and (value == 'auto' or
                    len(value) <= 200 and re.fullmatch(r'[a-zA-Z0-9._:-]+\.monitor', value))
        elif key == 'offset':
            valid = type(value) in (int, float) and math.isfinite(value) and abs(value) <= 60
        elif key == 'aliases' and isinstance(value, list):
            filtered = list(dict.fromkeys(v for v in value if isinstance(v, str) and
                       re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,31}', v) and v != 'tlyrics'))
            valid = not strict or len(filtered) == len(value)
            value = filtered
        elif key == 'song_offsets' and isinstance(value, dict):
            filtered = {k: v for k, v in value.items() if isinstance(k, str) and re.fullmatch(r'[0-9a-f]{64}', k)
                        and type(v) in (int, float) and math.isfinite(v) and abs(v) <= 60}
            valid = not strict or len(filtered) == len(value)
            value = filtered
        if valid:
            result[key] = value
        elif strict:
            raise ValueError(f'Invalid or unsupported setting: {key}')
    return result


def read_json(path):
    with Path(path).open('rb') as handle:
        content = handle.read(4 * 1024 * 1024 + 1)
    if len(content) > 4 * 1024 * 1024:
        raise ValueError('Settings file is too large.')
    def invalid_number(value):
        raise ValueError(f'Invalid JSON number: {value}')
    try:
        data = json.loads(content, parse_constant=invalid_number)
    except RecursionError:
        raise ValueError('Settings are nested too deeply.') from None
    if not isinstance(data, dict):
        raise ValueError('Settings must be a JSON object.')
    return data


def stored():
    for path in (CONFIG, CONFIG.with_suffix('.json.bak')):
        try:
            return read_json(path)
        except (OSError, ValueError):
            pass
    return {}


def load():
    return Preferences(normalise(stored()))


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(data, handle, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def export_file(path, config):
    path = Path(path).expanduser()
    values = normalise(dict(config), strict=True)
    values.pop('aliases')  # Local shell commands are deliberately not portable.
    payload = {'format': 'tlyrics-settings', 'version': 1, 'settings': values}
    path.parent.mkdir(parents=True, exist_ok=True)
    # A typo in the destination must never overwrite an unrelated file.
    with path.open('x', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write('\n')
    return path


def import_file(path, current):
    data = read_json(Path(path).expanduser())
    if data.get('format') == 'tlyrics-settings':
        if type(data.get('version')) is not int or data['version'] != 1:
            raise ValueError('Unsupported settings export version.')
        data = data.get('settings')
    if not isinstance(data, dict) or not data:
        raise ValueError('No settings found in the file.')
    normalise(data, strict=True)  # Validate the entire import before changing anything.
    merged = dict(current)
    merged.update({key: value for key, value in data.items() if key != 'aliases'})
    result = Preferences(normalise(merged), getattr(current, 'baseline', current))
    result.forced = set(getattr(current, 'forced', ())) | (set(data) - {'aliases'})
    return result


def song_key(track):
    return hashlib.sha256(json.dumps(track.key).encode()).hexdigest()


def save(config):
    validated = normalise(dict(config), strict=True)
    baseline = getattr(config, 'baseline', {})
    changed = {key: value for key, value in validated.items() if key in getattr(config, 'forced', ()) or key not in baseline or value != baseline[key]}
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = stored()
        merged = dict(previous)
        merged.update(normalise(previous))
        if 'song_offsets' in changed and 'song_offsets' in baseline and 'song_offsets' not in getattr(config, 'forced', ()):
            offsets = dict(merged['song_offsets'])
            old = baseline['song_offsets']
            for key in old.keys() - changed['song_offsets'].keys():
                offsets.pop(key, None)
            offsets.update({key: value for key, value in changed['song_offsets'].items() if key not in old or value != old[key]})
            changed['song_offsets'] = offsets
        merged.update(changed)
        if previous:
            atomic_write(CONFIG.with_suffix('.json.bak'), previous)
        atomic_write(CONFIG, merged)
    adopt(config, normalise(merged))


def alias_content():
    return '#!/bin/sh\n# tlyrics managed alias\nexec ' + shlex.quote(str(BIN / 'tlyrics')) + ' "$@"\n'


def add_alias(name):
    if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,31}', name) or name == 'tlyrics':
        raise ValueError('Use 1-32 letters, digits, underscores or hyphens; start with a letter.')
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
