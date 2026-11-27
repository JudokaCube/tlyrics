#!/usr/bin/python3
"""Terminal Lyrics - synced lyrics for desktop and browser music players."""
import argparse
import diagnostics
import experience
import bisect
import concurrent.futures
import curses
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
import settings
import browser_bridge
import lyrics
import rendering
import visualizer
import players as player_catalog
import updates
from timing import PlaybackClock
import math
import threading
from email.utils import parsedate_to_datetime

PREFIX = 'org.mpris.MediaPlayer2.'
IFACE = PREFIX + 'Player'
PATH = '/org/mpris/MediaPlayer2'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'tlyrics'


def parse_lrc(text):
    return [(line.start, line.text) for line in lyrics.parse_lrc(text).lines]


def active_line(lines, position):
    return bisect.bisect_right([line[0] for line in lines], position) - 1


@dataclass
class Track:
    player: str
    title: str
    artist: str
    album: str
    duration: float
    position: float | None
    status: str
    rate: float = 1.0
    sampled: float = field(default_factory=time.monotonic)
    service: str = ""
    track_id: str = ""
    resolution: float = 0.001
    seek_serial: int = 0

    def seconds(self, now=None):
        if self.position is None:
            return None
        elapsed = max(0, (time.monotonic() if now is None else now) - self.sampled)
        value = self.position + (min(elapsed, 2) * self.rate if self.status == "Playing" else 0)
        return max(0, min(value, self.duration) if self.duration > 0 else value)

    @property
    def key(self):
        return (self.title, self.artist, self.album, round(self.duration))


class Players:
    def __init__(self):
        try:
            import dbus
        except ImportError:
            raise RuntimeError('Install python-dbus (Arch) or python3-dbus (Debian/Ubuntu).')
        self.dbus = dbus
        self.bridge = None
        self.changed = threading.Event()
        self.seek_serials = {}
        self.signal_loop = None
        self.signal_matches = []
        self.signal_thread = None
        try:
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
        except ImportError:
            self.bus = dbus.SessionBus()
        else:
            self.bus = dbus.SessionBus(private=True, mainloop=DBusGMainLoop())
            self.signal_matches.append(self.bus.add_signal_receiver(
                self.on_seek, signal_name='Seeked', dbus_interface=IFACE, path=PATH, sender_keyword='sender'))
            self.signal_matches.append(self.bus.add_signal_receiver(
                lambda *args: self.changed.set(), signal_name='PropertiesChanged',
                dbus_interface='org.freedesktop.DBus.Properties', path=PATH, arg0=IFACE))
            self.signal_matches.append(self.bus.add_signal_receiver(
                self.on_owner, signal_name='NameOwnerChanged', dbus_interface='org.freedesktop.DBus'))
            self.signal_loop = GLib.MainLoop()
            self.signal_thread = threading.Thread(target=self.signal_loop.run, daemon=True)
            self.signal_thread.start()

    def on_seek(self, position, sender=None):
        self.seek_serials[sender] = self.seek_serials.get(sender, 0) + 1
        self.changed.set()

    def on_owner(self, name, old, new):
        if str(name).startswith(PREFIX):
            self.seek_serials.pop(str(old), None)
            self.changed.set()

    def close(self):
        for match in self.signal_matches:
            match.remove()
        if self.signal_loop:
            self.signal_loop.quit()
        if self.signal_thread:
            self.signal_thread.join(timeout=1)

    def read(self):
        tracks = []
        for name in sorted(self.bus.list_names()):
            if not name.startswith(PREFIX):
                continue
            try:
                obj = self.bus.get_object(name, PATH)
                props = self.dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
                p = props.GetAll(IFACE, timeout=0.5)
                m = p.get('Metadata', {})
                service = classify(str(name), str(m.get('xesam:url', '')))
                if service is None:
                    continue
                position = None
                try:
                    before = time.monotonic()
                    position = props.Get(IFACE, 'Position', timeout=0.5)
                    measured = (before + time.monotonic()) / 2
                except self.dbus.DBusException:
                    pass
                sampled = measured if position is not None else time.monotonic()
                duration = float(m.get('mpris:length', 0)) / 1e6
                rate = float(p.get('Rate', 1))
                if not math.isfinite(duration) or duration < 0 or not math.isfinite(rate) or not 0 <= rate <= 16:
                    continue
                if position is not None and (not math.isfinite(float(position)) or float(position) < 0):
                    position = None
                artists = m.get('xesam:artist', [])
                if isinstance(artists, str):
                    artists = [artists]
                serial = 0
                if getattr(self, 'signal_loop', None):
                    serial = self.seek_serials.get(str(self.bus.get_name_owner(name)), 0)
                tracks.append(Track(str(name)[len(PREFIX):], str(m.get('xesam:title', '')),
                                    ', '.join(map(str, artists)),
                                    str(m.get('xesam:album', '')), duration,
                                    None if position is None else float(position) / 1e6,
                                    str(p.get('PlaybackStatus', 'Stopped')), rate, sampled, service,
                                    str(m.get('mpris:trackid', '')), 0.001, serial))
            except (self.dbus.DBusException, TypeError, ValueError, OverflowError):
                continue
        bridge = self.bridge
        if bridge:
            for data, stamp in bridge.read():
                tracks.append(Track(data['service'] + '.' + data['session'][:8], data['title'],
                                    data['artist'], data['album'], data['duration'], data['position'],
                                    data['status'], data['rate'], stamp, data['service'], data.get('track_id', ''),
                                    data.get('resolution', 1.0 if data['service'] == 'spotify-web' else 0.001),
                                    data.get('seek_serial', 0)))
        return tracks


def classify(name, url=''):
    return player_catalog.classify(name, url)


def select_player(tracks, preferred=None, previous=None):
    tracks = [t for t in tracks if player_catalog.accepted(t)]
    if preferred and preferred != 'auto':
        groups = {'spotify': ('spotify', 'spotify-web'),
                  'youtube-music': ('youtube-music', 'youtube-music-web'),
                  'browser': tuple(set(player_catalog.WEB_SERVICES.values()))}
        tracks = [t for t in tracks if t.service in groups.get(preferred, (preferred,))
                  or preferred.lower() in t.player.lower()]
    return max(tracks, key=lambda t: (t.status == 'Playing', bool(t.title), bool(t.artist),
                                     t.resolution < 0.5, t.player == previous, t.service.endswith('-web'),
                                     t.status == 'Paused'), default=None)


REQUEST_LOCK = threading.Lock()
NEXT_REQUEST = 0.0
RETRY_UNTIL = 0.0


def fetch_record(params, endpoint='get'):
    global NEXT_REQUEST, RETRY_UNTIL
    if endpoint not in ('get', 'search'):
        raise ValueError('Unsupported lyrics request')
    url = 'https://lrclib.net/api/' + endpoint + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': f'tlyrics/{updates.VERSION} (https://github.com/JudokaCube/tlyrics)'})
    with REQUEST_LOCK:
        now = time.monotonic()
        if now < RETRY_UNTIL:
            return {'message': f'Lyrics service rate limit. Retry in {math.ceil(RETRY_UNTIL - now)} seconds.'}
        if now < NEXT_REQUEST:
            time.sleep(min(0.3, NEXT_REQUEST - now))
        try:
            with urllib.request.urlopen(req, timeout=6) as response:
                payload = response.read(lyrics.MAX_BYTES + 1)
                if len(payload) > lyrics.MAX_BYTES:
                    return {'message': 'Lyrics response is too large.'}
                data = json.loads(payload)
        except urllib.error.HTTPError as exc:
            try:
                if exc.code == 404:
                    return {'message': 'No matching lyrics found. Press r to retry, or use --lrc FILE.'}
                if exc.code in (429, 503):
                    header = exc.headers.get('Retry-After', '60') if exc.headers else '60'
                    try:
                        delay = float(header)
                        if not math.isfinite(delay):
                            raise ValueError('Invalid retry interval')
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(header).timestamp() - time.time()
                        except (TypeError, ValueError, OverflowError):
                            delay = 60
                    RETRY_UNTIL = time.monotonic() + max(1, delay)
                    return {'message': f'Lyrics service rate limit. Retry in {math.ceil(max(1, delay))} seconds.'}
                return {'message': f'Lyrics service returned HTTP {exc.code}. Press r to retry.'}
            finally:
                exc.close()
        except (OSError, ValueError) as exc:
            return {'message': f'Lyrics unavailable: {exc}. Press r to retry.'}
        finally:
            NEXT_REQUEST = time.monotonic() + 0.25
    if endpoint == 'search':
        if not isinstance(data, list) or len(data) > 20 or not all(valid_lyrics(record) for record in data):
            return {'message': 'Invalid lyrics search response.'}
        return data
    if not valid_lyrics(data):
        return {'message': 'Invalid lyrics response. Press r to retry.'}
    return data


def get_lyrics(track, refresh=False, cache_enabled=True):
    digest = settings.song_key(track)
    cache = CACHE / (digest + '.json')
    cached = None
    if not refresh and cache_enabled:
        try:
            data = json.loads(cache.read_text())
            if valid_lyrics(data):
                cached = data
                if (data.get('_tlyrics_checked_words') or 'lyricsfile' in data) and time.time() - cache.stat().st_mtime < 7 * 86400:
                    return data
        except (OSError, ValueError):
            pass
    params = {'track_name': track.title, 'artist_name': track.artist}
    if track.album:
        params['album_name'] = track.album
    if 1 <= track.duration <= 3600:
        params['duration'] = str(round(track.duration))
    data = fetch_record(params)
    if data.get('message'):
        return cached or data
    data['_tlyrics_checked_words'] = True
    try:
        if not cache_enabled:
            return data
        CACHE.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(f'.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(data))
        temporary.replace(cache)
    except OSError:
        pass
    return data


def valid_lyrics(data):
    return (isinstance(data, dict)
            and any(key in data for key in ('syncedLyrics', 'plainLyrics', 'lyricsfile', 'instrumental'))
            and all(data.get(key) is None or isinstance(data[key], str)
                    for key in ('syncedLyrics', 'plainLyrics', 'lyricsfile')))


def load_lyrics(track, refresh=False, cache_enabled=True):
    try:
        record = experience.selected(track)
        if record is not None and not valid_lyrics(record):
            raise ValueError('Invalid saved lyrics selection')
    except (OSError, ValueError) as exc:
        diagnostics.record('Saved lyrics selection failed', exc)
        record = None
    if record is None:
        record = get_lyrics(track, refresh, cache_enabled)
    return record, lyrics.from_record(record)


def clean(text):
    return ''.join(c for c in str(text) if c.isprintable())


def put(screen, y, text, attr=0, centered=False):
    h, w = screen.getmaxyx()
    if 0 <= y < h and w > 2:
        text = rendering.clip_text(text, w - 2)
        cells = sum(rendering.width(c) for c in text)
        x = max(1, (w - cells) // 2) if centered else 1
        try:
            screen.addnstr(y, x, text, len(text), attr)
        except curses.error:
            pass


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def apply_theme(config):
    return rendering.configure(config)


def small_terminal(screen, rows=14, columns=24):
    h, w = screen.getmaxyx()
    if h >= rows and w >= columns:
        return False
    put(screen, max(0, h // 2 - 1), 'Resize terminal', curses.A_BOLD, True)
    put(screen, h // 2 + 1, f'Need {columns} columns x {rows} rows', curses.A_DIM, True)
    return True


def setting_label(label, value, columns):
    if columns < 2:
        return rendering.clip_text(value, columns)
    value = rendering.clip_text(value, max(1, columns // 2))
    room = max(0, columns - sum(rendering.width(c) for c in value) - 1)
    label = rendering.clip_text(label, room)
    return label + ' ' * (room - sum(rendering.width(c) for c in label) + 1) + value


def prompt(screen, label):
    value = ''
    while True:
        screen.erase()
        if not small_terminal(screen, 8, 16):
            put(screen, 1, label, curses.A_BOLD)
            put(screen, 3, rendering.clip_text(value, screen.getmaxyx()[1] - 3, tail=True) + '▏')
            put(screen, screen.getmaxyx()[0] - 2, 'Enter confirms · Esc cancels', curses.A_DIM)
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key == curses.KEY_RESIZE:
            screen.clearok(True)
            continue
        if key in ('\x1b', '\x03'):
            return None
        if key in ('\n', '\r'):
            return value.strip()
        if key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            value = value[:-1]
        elif isinstance(key, str) and key.isprintable() and len(value) < 4096:
            value += key


def choose(screen, title, values, current):
    index = values.index(current) if current in values else 0
    while True:
        screen.erase()
        if not small_terminal(screen, 8, 16):
            put(screen, 1, title, curses.A_BOLD)
            room = max(1, screen.getmaxyx()[0] - 6)
            start = max(0, index - room + 1)
            for row, value in enumerate(values[start:start + room]):
                put(screen, row + 3, str(value), curses.A_REVERSE if row + start == index else 0)
            put(screen, screen.getmaxyx()[0] - 2, '↑ / ↓ choose · Enter select · Esc back', curses.A_DIM)
        screen.refresh()
        key = screen.getch()
        if key == curses.KEY_RESIZE:
            screen.clearok(True)
            continue
        if key in (27, 3):
            return current
        if key in (10, 13):
            return values[index]
        if key == curses.KEY_UP:
            index = (index - 1) % len(values)
        elif key == curses.KEY_DOWN:
            index = (index + 1) % len(values)


SETTINGS_GROUPS = {
    'Playback & lyrics': [('player', 'Music player'), ('word_sync', 'Word sync'),
                          ('offset', 'Global timing offset'), ('offset_step', 'Song adjustment step'),
                          ('interval', 'Playback refresh (seconds)'), ('cache', 'Cache lyrics'),
                          ('center_active', 'Keep playing line centered')],
    'Appearance': [('theme', 'Theme'), ('layout', 'Layout'), ('align', 'Alignment'),
                   ('spacing', 'Line spacing'), ('max_width', 'Lyrics width (0 = full)'),
                   ('progress', 'Progress bar'), ('timestamps', 'Lyric timestamps')],
    'Effects': [('word_style', 'Word highlight'), ('glow', 'Glow'), ('gradient', 'Gradient target'),
                ('palette', 'Gradient colours'), ('pulse', 'Gentle pulse'),
                ('animate_gradient', 'Animate gradient'), ('dim_inactive', 'Dim inactive lyrics'),
                ('underline_word', 'Underline active word'), ('fps', 'Animation frame rate')],
    'Browser setup': [('bridge', 'Browser bridge'), ('browser', 'Export browser setup script')],
    'Updates': [('update_mode', 'Update mode'), ('update_hours', 'Check interval (hours)'),
                ('check_updates', 'Save and check for updates')],
    'Command aliases': [('add', 'Add terminal alias'), ('remove', 'Remove terminal alias')],
}


ROOT_CATEGORIES = tuple(SETTINGS_GROUPS) + ('Visualizer', 'Settings backup', 'Diagnostics')
SETTINGS_GROUPS['Appearance'] += [('preset', 'Apply appearance preset'), ('Layout details', 'Layout details >')]
SETTINGS_GROUPS['Appearance'] += [('preview', 'Live appearance preview')]
SETTINGS_GROUPS.update({
    'Diagnostics': [('show_diagnostics', 'Show technical details'), ('log_level', 'Logging'),
                    ('log_files', 'Retained log files'), ('view_logs', 'View diagnostics'),
                    ('export_report', 'Export bug report'), ('clear_logs', 'Clear diagnostics')],
    'Settings backup': [('export_settings', 'Export settings'), ('import_settings', 'Import settings')],
    'Layout details': [('background', 'Background'), ('border', 'Border'), ('title_style', 'Song title style'),
                       ('show_header', 'App heading'), ('show_status', 'Playback details'),
                       ('show_hints', 'Keyboard hints'), ('progress_style', 'Progress style'),
                       ('active_marker', 'Active lyric marker')],
    'Visualizer': [('visualizer', 'Enable visualizer'), ('viz_layout', 'Layout'),
                   ('viz_position', 'Position'), ('viz_height', 'Height (rows)'),
                   ('viz_width', 'Width (0 = auto)'), ('Audio & response', 'Audio & response >'),
                   ('Visualizer style', 'Style & colours >'), ('viz_preset', 'Apply visualizer preset')],
    'Audio & response': [('viz_backend', 'Audio backend'), ('viz_source', 'Output monitor (PulseAudio)'),
                         ('viz_bars', 'Frequency bands'), ('viz_channels', 'Channels'),
                         ('viz_fps', 'Analysis frame rate'), ('viz_autosens', 'Automatic sensitivity'),
                         ('viz_sensitivity', 'Manual sensitivity (%)'), ('viz_noise', 'Noise reduction (%)'),
                         ('viz_smoothing', 'Response smoothing'), ('viz_decay', 'Peak decay')],
    'Visualizer style': [('viz_colour', 'Colour mode'), ('viz_palette', 'Palette'),
                         ('viz_gradient', 'Gradient direction'), ('viz_glyph', 'Bar character'),
                         ('viz_gap', 'Bar spacing'), ('viz_peaks', 'Floating peak markers')],
})


def menu(screen, config, tracks):
    draft = settings.clone(config)
    category = None
    parents = []
    selections = {}
    theme_key = None
    index = 0
    message = ''
    screen.timeout(-1)
    try:
        while True:
            rows = (SETTINGS_GROUPS[category] + [('back', 'Back')] if category else
                    [(name, name + ' >') for name in ROOT_CATEGORIES] +
                    [('save', 'Save and return'), ('cancel', 'Discard and return')])
            new_theme = tuple(draft[k] for k in ('theme', 'palette', 'glow', 'background', 'viz_palette'))
            if new_theme != theme_key:
                accent = apply_theme(draft)
                theme_key = new_theme
            rendering.chrome(screen, draft)
            h, w = screen.getmaxyx()
            if not small_terminal(screen, 8, 16):
                heading = f'Settings / {category}' if category else f'Terminal Lyrics {updates.VERSION} / Settings'
                put(screen, 1, heading, accent)
                room = max(1, h - 7)
                start = max(0, index - room + 1)
                for row, (key, label) in enumerate(rows[start:start + room]):
                    value = draft.get(key, '')
                    if isinstance(value, bool):
                        value = 'on' if value else 'off'
                    display = setting_label(label, str(value), w - 2) if key in draft else label
                    put(screen, 3 + row, display, curses.A_REVERSE if row + start == index else 0)
                if category == 'Command aliases':
                    hint = 'Alias changes apply immediately. ' + (', '.join(draft['aliases']) or 'No aliases.')
                elif category == 'Visualizer style' and rows[index][0] == 'viz_peaks':
                    hint = 'Optional floating marks above bars show recent peaks. Off gives clean bars.'
                elif category == 'Playback & lyrics' and rows[index][0] == 'word_sync':
                    hint = 'Highlights supplied word timestamps; line-only lyrics cannot track words.'
                elif draft != config:
                    hint = 'Unsaved changes - Ctrl+S saves all categories.'
                else:
                    hint = 'Select an option to edit.' if category else 'Choose a category to change its options.'
                put(screen, h - 3, message or hint, curses.A_DIM)
                put(screen, h - 2, '↑↓ move · Enter edit · p preview · Ctrl+S save · Esc ' + ('back' if category else 'save & exit'), curses.A_DIM)
            screen.refresh()
            pressed = screen.getch()
            if pressed == curses.KEY_RESIZE:
                screen.clearok(True)
                continue
            if pressed == 3:
                return
            if pressed == ord('p'):
                appearance_preview(screen, draft)
                theme_key = None
                continue
            if pressed == 27:
                if category is None:
                    try:
                        if draft != config or getattr(draft, 'forced', None):
                            settings.save(draft)
                            settings.adopt(config, draft)
                        return
                    except (OSError, ValueError) as exc:
                        diagnostics.record('Settings save failed', exc)
                        message = f'Could not save: {exc}' if config['show_diagnostics'] else 'Could not save. See Diagnostics.'
                        continue
                selections[category] = index
                category, index = parents.pop()
                message = ''
                continue
            if pressed == curses.KEY_UP:
                index = (index - 1) % len(rows)
            elif pressed == curses.KEY_DOWN:
                index = (index + 1) % len(rows)
            elif pressed in (10, 13, 19):
                key = 'save' if pressed == 19 else rows[index][0]
                try:
                    if key in SETTINGS_GROUPS:
                        parents.append((category, index))
                        category, index, message = key, selections.get(key, 0), ''
                        continue
                    if key == 'back':
                        selections[category] = index
                        category, index = parents.pop()
                        message = ''
                        continue
                    if key == 'cancel':
                        return
                    if key == 'check_updates':
                        settings.save(draft)
                        settings.adopt(config, draft)
                        return 'check_updates'
                    if key == 'save':
                        settings.save(draft)
                        settings.adopt(config, draft)
                        return
                    if key == 'preview':
                        appearance_preview(screen, draft)
                        theme_key = None
                    elif key == 'viz_preset':
                        name = choose(screen, 'Visualizer preset', ['Cancel'] + list(settings.VISUALIZER_PRESETS), 'Cancel')
                        if name != 'Cancel':
                            draft.update(settings.VISUALIZER_PRESETS[name])
                            message = 'Preset applied. Press p to preview; Ctrl+S saves.'
                    elif key == 'view_logs':
                        view_logs(screen)
                    elif key == 'export_report':
                        path = prompt(screen, 'Export bug report to a new .json file')
                        if path:
                            message = f'Exported to {diagnostics.export(path, draft)}'
                    elif key == 'clear_logs':
                        if choose(screen, 'Clear local diagnostics?', ['No', 'Yes'], 'No') == 'Yes':
                            diagnostics.clear()
                            message = 'Diagnostics cleared.'
                    elif key == 'export_settings':
                        path = prompt(screen, 'Export path (new .json file; ~ is supported)')
                        if path:
                            message = f'Exported to {settings.export_file(path, draft)}'
                    elif key == 'import_settings':
                        path = prompt(screen, 'Import settings from .json file (~ is supported)')
                        if path:
                            draft = settings.import_file(path, draft)
                            message = 'Imported. Esc or Ctrl+S saves; Discard cancels.'
                    elif key == 'preset':
                        name = choose(screen, 'Appearance preset', ['Cancel'] + list(settings.PRESETS), 'Cancel')
                        if name != 'Cancel':
                            draft.update(settings.PRESETS[name])
                            message = f'{name.title()} preset applied. Ctrl+S to save.'
                    elif key == 'viz_source':
                        values = list(dict.fromkeys(visualizer.sources() + [draft[key]]))
                        draft[key] = choose(screen, 'Output monitor (PipeWire uses the default output)', values, draft[key])
                    elif key == 'player':
                        values = list(dict.fromkeys(['auto', 'spotify', 'youtube-music', 'browser', 'spotify-web',
                                                     'youtube-music-web', 'limusic', draft['player']] + [t.player for t in tracks]))
                        draft[key] = choose(screen, 'Supported music player', values, draft[key])
                    elif key in settings.CHOICES:
                        draft[key] = choose(screen, rows[index][1], settings.CHOICES[key], draft[key])
                    elif key == 'offset':
                        value = prompt(screen, 'Offset in seconds (-60 to 60; positive shows lyrics earlier)')
                        if value is not None:
                            number = float(value)
                            if not -60 <= number <= 60:
                                raise ValueError('Offset must be between -60 and 60 seconds.')
                            draft[key] = number
                    elif key == 'browser':
                        message = f'Install in a userscript manager: {browser_bridge.export_script()}'
                        draft['bridge'] = True
                    elif key == 'add':
                        name = prompt(screen, 'New command alias (example: lyrics)')
                        if name:
                            settings.add_alias(name)
                            config['aliases'] = list(dict.fromkeys(config['aliases'] + [name]))
                            draft['aliases'] = list(config['aliases'])
                            settings.save(config)
                            message = f'Created {name} in ~/.local/bin'
                    elif key == 'remove':
                        if not draft['aliases']:
                            message = 'No custom aliases.'
                        else:
                            name = choose(screen, 'Remove alias', ['Cancel'] + draft['aliases'], 'Cancel')
                            if name != 'Cancel':
                                settings.remove_alias(name)
                                config['aliases'].remove(name)
                                draft['aliases'] = list(config['aliases'])
                                settings.save(config)
                                message = f'Removed {name}'
                except (ValueError, OSError) as exc:
                    diagnostics.record('Settings action failed', exc)
                    message = str(exc) if config['show_diagnostics'] else 'Action failed. See Diagnostics.'
    finally:
        screen.timeout(max(1, round(1000 / config["fps"])))
        apply_theme(config)
        diagnostics.configure(config)


def appearance_preview(screen, draft):
    options = [('theme', 'Theme'), ('background', 'Background'), ('border', 'Border'),
               ('layout', 'Lyrics layout'), ('align', 'Alignment'), ('spacing', 'Line spacing'),
               ('gradient', 'Gradient'), ('palette', 'Lyric palette'), ('glow', 'Glow'),
               ('word_style', 'Word highlight'), ('visualizer', 'Visualizer'),
               ('viz_layout', 'Visualizer layout'), ('viz_palette', 'Visualizer palette'),
               ('viz_height', 'Visualizer height'), ('viz_smoothing', 'Visualizer smoothing')]
    doc = lyrics.parse_lrc('[00:00]<00:00>Make <00:01>your <00:02>terminal <00:03>your own\n'
                           '[00:04]<00:04>Colours <00:05>move <00:06>with <00:07>the music<00:08>')
    engine = visualizer.Visualizer()  # Synthetic frames only; no audio process.
    index, started = 0, time.monotonic()
    apply_theme(draft)
    screen.timeout(33)
    try:
        while True:
            now = time.monotonic()
            position = (now - started) % 8
            rendering.chrome(screen, draft)
            h, w = screen.getmaxyx()
            if not small_terminal(screen, 14, 24):
                put(screen, 0, 'Appearance preview (sample)', curses.A_BOLD)
                put(screen, 2, 'Sample lyrics and simulated audio', centered=True)
                top, bottom, panel = visualizer.region(h, draft)
                rendering.draw(screen, doc, position, draft, top=top, bottom=bottom)
                engine.latest = tuple(.15 + .65 * (1 + math.sin(now * 2 + i * .35)) / 2 for i in range(draft['viz_bars']))
                engine.stamp = now
                visualizer.draw(screen, engine, draft, panel)
                key, label = options[index]
                put(screen, 4, f'{label}: {draft[key]}', curses.A_REVERSE, True)
                put(screen, 5, '↑↓ option · ←→ change · Esc back', centered=True)
                put(screen, h - 2, 'Changes stay pending until settings are saved.', curses.A_DIM)
            screen.refresh()
            pressed = screen.getch()
            if pressed in (27, 3, 10, 13):
                return
            if pressed == curses.KEY_RESIZE:
                screen.clearok(True)
            elif pressed in (curses.KEY_UP, curses.KEY_DOWN):
                index = (index + (1 if pressed == curses.KEY_DOWN else -1)) % len(options)
            elif pressed in (curses.KEY_LEFT, curses.KEY_RIGHT):
                key = options[index][0]
                values = settings.CHOICES[key]
                draft[key] = values[(values.index(draft[key]) + (1 if pressed == curses.KEY_RIGHT else -1)) % len(values)]
                apply_theme(draft)
    finally:
        screen.timeout(-1)


def wait_source(screen, future):
    screen.timeout(100)
    try:
        while not future.done():
            screen.erase()
            put(screen, 2, 'Finding matching lyrics...')
            put(screen, 4, 'Esc cancels')
            screen.refresh()
            key = screen.getch()
            if key in (27, 3):
                future.cancel()
                return None
            if key == curses.KEY_RESIZE:
                screen.clearok(True)
        return future.result()
    finally:
        screen.timeout(-1)


def source_menu(screen, track, data, doc, pool, cache_enabled=True):
    screen.timeout(-1)
    message = experience.source_summary(data, doc)
    while True:
        action = choose(screen, message, ['Back', 'Find another LRCLIB version', 'Load local lyrics file',
                                        'Use automatic lookup'], 'Back')
        try:
            if action == 'Back':
                return None
            if action == 'Use automatic lookup':
                result = wait_source(screen, pool.submit(get_lyrics, track, True, cache_enabled))
                if result is None:
                    continue
                if result.get('message'):
                    raise ValueError('Lookup failed. Your current lyrics were kept.')
                parsed = lyrics.from_record(result)
                if not parsed.lines and not parsed.plain and not result.get('instrumental'):
                    raise ValueError('No usable lyrics found. Your current lyrics were kept.')
                experience.automatic(track)
                return result, parsed
            if action == 'Load local lyrics file':
                path = prompt(screen, 'Local .lrc or .lyricsfile.yaml path (~ supported)')
                if not path:
                    continue
                record = experience.local_record(path)
            else:
                found = wait_source(screen, pool.submit(fetch_record,
                                    {'track_name': track.title, 'artist_name': track.artist}, 'search'))
                if found is None:
                    continue
                if not isinstance(found, list):
                    raise ValueError('Search unavailable. Your current lyrics were kept.')
                if not found:
                    message = 'No matching versions found. Try a local file.'
                    continue
                candidates = []
                for record in found:
                    parsed = lyrics.from_record(record)
                    timing = 'words' if parsed.has_words else 'lines' if parsed.lines else 'untimed'
                    duration = record.get('duration')
                    length = clock(duration) if type(duration) in (int, float) and math.isfinite(duration) else '?'
                    candidates.append(f"{len(candidates) + 1}. {timing} · {length} · {record.get('albumName') or record.get('trackName') or 'Unknown'}")
                choice = choose(screen, 'Choose the recording matching your song', ['Cancel'] + candidates, 'Cancel')
                if choice == 'Cancel':
                    continue
                record = dict(found[candidates.index(choice)])
                record['_tlyrics_source'] = f"LRCLIB selection #{record.get('id', '?')}"
            parsed = experience.select(track, record)
            return record, parsed
        except (OSError, ValueError) as exc:
            diagnostics.record('Lyrics source action failed', exc)
            message = 'Could not use that source. Current lyrics kept; see Diagnostics.'


def view_logs(screen):
    offset = 0
    while True:
        screen.erase()
        h, w = screen.getmaxyx()
        lines = []
        for line in diagnostics.read().splitlines()[-500:]:
            rows = rendering.wrap([(c, 0, False) for c in line], max(1, w - 2))
            lines.extend(''.join(c for c, _, _ in row) for row in rows)
        room = max(1, h - 5)
        offset = min(offset, max(0, len(lines) - room))
        put(screen, 0, 'Diagnostics (local)', curses.A_BOLD)
        for i, line in enumerate(lines[offset:offset + room]):
            put(screen, i + 2, line)
        put(screen, h - 2, '↑↓ scroll · Esc back', curses.A_DIM)
        screen.refresh()
        key = screen.getch()
        if key in (27, 3, ord('q')):
            return
        if key == curses.KEY_DOWN:
            offset += 1
        elif key == curses.KEY_UP:
            offset = max(0, offset - 1)
        elif key == curses.KEY_RESIZE:
            screen.clearok(True)


def setup_input(screen):
    curses.set_escdelay(25)
    screen.keypad(True)
    screen.notimeout(False)


def ui(screen, players, args, config):
    setup_input(screen)
    curses.raw()  # Let Ctrl+S reach the menu instead of terminal flow control.
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.timeout(max(1, round(1000 / config["fps"])))
    accent = apply_theme(config)
    current = None
    key = None
    data = {}
    doc = lyrics.Document()
    playback = PlaybackClock()
    future = None
    polling = None
    next_poll = 0
    scroll = 0
    calibration = None
    calibration_message = ''
    notice, notice_until = '', 0.0
    error = ''
    bridge_error = ''
    available = []
    local = None
    if args.lrc:
        path = Path(args.lrc)
        if path.stat().st_size > lyrics.MAX_BYTES:
            raise ValueError('Lyrics file is too large')
        local = {('lyricsfile' if path.suffix in ('.yaml', '.yml') else 'syncedLyrics'): path.read_text(encoding='utf-8-sig')}
    check_updates = args.settings and menu(screen, config, []) == 'check_updates'
    if args.settings:
        accent = apply_theme(config)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    lyrics_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    updater = updates.Manager()
    visuals = visualizer.Visualizer()
    try:
        if check_updates:
            updater.check(config, manual=True)
        while True:
            frame_started = time.monotonic()
            visuals.configure(config if screen.getmaxyx()[0] >= 18 and screen.getmaxyx()[1] >= 24 else dict(config, visualizer=False))
            restart = updater.poll()
            if restart:
                return restart
            if updater.pending:
                release, updater.pending = updater.pending, None
                screen.timeout(-1)
                try:
                    approved = config['update_mode'] == 'auto' and not updater.manual
                    if not approved:
                        approved = choose(screen, f'Install tlyrics {release.version}? Restarts after updating.', ['No', 'Yes'], 'No') == 'Yes'
                    if approved:
                        updater.install(release)
                    else:
                        updater.message = f'Update {release.version} skipped. Press u to check again.'
                finally:
                    screen.timeout(max(1, round(1000 / config['fps'])))
            updater.check(config)
            now = time.monotonic()
            if config['bridge'] and players.bridge is None:
                try:
                    players.bridge = browser_bridge.Bridge()
                    bridge_error = ''
                except OSError as exc:
                    config['bridge'] = False
                    bridge_error = f'Browser bridge unavailable: {exc}'
                    diagnostics.record('Browser bridge failed', exc)
            elif not config['bridge'] and players.bridge:
                players.bridge.close()
                players.bridge = None
            if players.changed.is_set():
                next_poll = 0
                players.changed.clear()
            if polling is None and now >= next_poll:
                polling = pool.submit(players.read)
                next_poll = now + config["interval"]
            if polling is not None and polling.done():
                try:
                    available = polling.result()
                    current = select_player(available, config["player"],
                                            current.player if current else None)
                    error = ''
                except Exception as exc:
                    current = None
                    error = str(exc)
                    diagnostics.record('Player polling failed', exc)
                polling = None
            playback.poll_interval = config["interval"]
            playback.update(current)
            new_key = current.key if current and current.title else None
            if new_key != key:
                key = new_key
                calibration = None
                data, doc, scroll = {}, lyrics.Document(), 0
                if future:
                    future.cancel()
                future = None
                if key:
                    if local:
                        data = local
                        doc = lyrics.from_record(data)
                    else:
                        future = lyrics_pool.submit(load_lyrics, current, False, config["cache"])
            if future and future.done():
                try:
                    result, parsed = future.result()
                except Exception as exc:
                    diagnostics.record('Lyrics loading failed', exc)
                    result = {'message': f'Lyrics unavailable: {exc}'}
                    parsed = lyrics.Document()
                if result.get('message') and (doc.lines or doc.plain):
                    doc.warning = result['message']
                else:
                    data = result
                    doc = parsed
                    if calibration:
                        calibration = None
                        notice, notice_until = 'Lyrics changed; calibration cancelled.', time.monotonic() + 4
                future = None
                if doc.warning:
                    diagnostics.record('Lyrics parsing warning')
                if result.get('message'):
                    diagnostics.record('Lyrics lookup unavailable')
            rendering.chrome(screen, config)
            h, w = screen.getmaxyx()
            if not small_terminal(screen):
                lyric_top, lyric_bottom, panel = visualizer.region(h, dict(config, visualizer=False) if calibration else config)
                if config['show_header']:
                    put(screen, 0, f'tlyrics {updates.VERSION}', accent)
                if updater.message and config['show_diagnostics'] and not calibration:
                    put(screen, 1, updater.message, curses.A_DIM)
                if bridge_error and config['show_diagnostics'] and not calibration:
                    put(screen, 1, bridge_error)
                if current and current.title:
                    put(screen, 2, current.title, {'bold': curses.A_BOLD, 'accent': accent, 'plain': 0}[config['title_style']], True)
                    put(screen, 3, current.artist, curses.A_DIM, True)
                    position = playback.seconds()
                    song_offset = config['song_offsets'].get(settings.song_key(current), 0.0)
                    offset = config['offset'] + song_offset
                    stamp = clock(position) if position is not None else '--:--'
                    if config['show_status'] and not calibration:
                        put(screen, 5, (f'{current.player} · {current.status} · {stamp} / {clock(current.duration)} · offset {offset:+.2f}s' if config['show_diagnostics'] else f'{stamp} / {clock(current.duration)}'), centered=True)
                    if config["progress"] and position is not None and current.duration:
                        length = max(1, w - 6)
                        put(screen, 6, rendering.progress_bar(position / current.duration, length, config['progress_style']), accent)
                    room = max(1, (lyric_bottom - lyric_top) // config['spacing'])
                    if doc.lines and position is not None:
                        lyric_time = position + offset
                        mode = doc.sync_mode(lyric_time, config['word_sync'])
                        if current.resolution >= 0.5:
                            mode += ' | coarse player timing'
                        elif playback.cadence >= 0.5:
                            mode += ' | interpolating player clock'
                        if config['show_diagnostics'] and not calibration:
                            put(screen, 7, mode + (' | ' + doc.warning if doc.warning else ''), curses.A_DIM, True)
                        if calibration:
                            target = calibration.lines[calibration.index]
                            put(screen, 5, f'Calibration · {len(calibration.samples)} distinct taps', centered=True)
                            put(screen, 7, 'Tap Space as this line begins:', curses.A_BOLD, True)
                            if calibration_message:
                                put(screen, 1, calibration_message, curses.A_DIM)
                            rendering.draw(screen, lyrics.Document([lyrics.Line(0, target.text)]), 0,
                                           dict(config, layout='focus', word_sync=False), top=lyric_top, bottom=lyric_bottom)
                        else:
                            rendering.draw(screen, doc, lyric_time, config, top=lyric_top, bottom=lyric_bottom)
                    elif future:
                        put(screen, (lyric_top + lyric_bottom) // 2, 'Finding lyrics…', centered=True)
                    else:
                        plain = doc.plain or data.get('plainLyrics') or ''
                        message = ((data.get('message') or doc.warning) if config['show_diagnostics'] else '') or ('Instrumental ♪' if data.get('instrumental') else
                                  'Player does not report position; sync unavailable.' if doc.lines else
                                  'Only unsynced lyrics available. Use ↑ / ↓ to scroll.' if plain else 'No lyrics available.')
                        put(screen, 7, message, curses.A_DIM, True)
                        display = [line.text for line in doc.lines] if doc.lines else plain.splitlines()
                        scroll = min(scroll, max(0, len(display) - room))
                        for row, words in enumerate(display[scroll:scroll + room]):
                            put(screen, lyric_top + row * config['spacing'], words, centered=config['align'] == 'center')
                else:
                    put(screen, (lyric_top + lyric_bottom) // 2, (error if config['show_diagnostics'] else '') or 'Waiting for a supported music player...', centered=True)
                visualizer.draw(screen, visuals, config, panel)
                if notice and time.monotonic() < notice_until:
                    put(screen, 1, notice, curses.A_DIM)
                if config['show_hints'] or calibration:
                    put(screen, h - 2, ('Space tap · ←→ line · Enter review · Backspace reset · Esc cancel' if calibration else
                                       'q quit  s settings  c calibrate  l lyrics  v visualizer  [/] timing  r retry'), curses.A_DIM, True)
            screen.refresh()
            # Drawing time is part of the frame budget, not an extra delay.
            remaining = 1 / config['fps'] - (time.monotonic() - frame_started)
            screen.timeout(max(0, math.ceil(remaining * 1000)))
            pressed = screen.getch()
            if pressed == curses.KEY_RESIZE:
                screen.clearok(True)
                continue
            if calibration:
                if (h < 14 or w < 24) and pressed not in (27, 3, ord('q'), ord('c')):
                    continue
                if pressed in (27, 3, ord('c')):
                    calibration = None
                elif pressed in (curses.KEY_LEFT, curses.KEY_RIGHT):
                    calibration.move(1 if pressed == curses.KEY_RIGHT else -1)
                elif pressed in (curses.KEY_BACKSPACE, 127, 8):
                    calibration.samples.clear()
                    calibration_message = ''
                elif pressed in (ord(' '), 10, 13):
                    try:
                        if pressed == ord(' '):
                            if not current or current.status != 'Playing':
                                raise ValueError('Resume the song before tapping.')
                            calibration.tap(playback.seconds())
                            calibration_message = ''
                        else:
                            adjustment = calibration.adjustment(config['offset'])
                            screen.timeout(-1)
                            decision = choose(screen, f'Save song offset {adjustment:+.3f}s?', ['Keep tapping', 'Save'], 'Keep tapping')
                            screen.timeout(max(1, round(1000 / config['fps'])))
                            if decision == 'Save':
                                draft = settings.clone(config)
                                draft['song_offsets'][settings.song_key(current)] = adjustment
                                settings.save(draft)
                                settings.adopt(config, draft)
                                calibration = None
                                notice, notice_until = 'Song calibration saved.', time.monotonic() + 4
                    except ValueError as exc:
                        calibration_message = str(exc)
                    except OSError as exc:
                        diagnostics.record('Calibration save failed', exc)
                        calibration_message = 'Could not save. See Diagnostics.'
                if pressed != ord('q'):
                    continue
            if pressed in (ord('q'), 27, 3):
                break
            if pressed == ord('c'):
                position = playback.seconds()
                if current and position is not None and doc.lines:
                    try:
                        calibration = experience.Calibration(doc, position,
                            config['offset'] + config['song_offsets'].get(settings.song_key(current), 0))
                        calibration_message = ''
                        notice = ''
                    except ValueError as exc:
                        notice, notice_until = str(exc), time.monotonic() + 4
                else:
                    notice, notice_until = 'Calibration needs a song with timed lyrics.', time.monotonic() + 4
            if pressed == ord('l') and current:
                try:
                    result = source_menu(screen, current, data, doc, lyrics_pool, config['cache'])
                    if result:
                        if future:
                            future.cancel()
                        future = None
                        local = None
                        data, doc = result
                        scroll = 0
                    next_poll = 0
                finally:
                    screen.timeout(max(1, round(1000 / config['fps'])))
            if pressed == ord('s'):
                if menu(screen, config, available) == 'check_updates':
                    updater.check(config, manual=True)
                accent = apply_theme(config)
                next_poll = 0
            if pressed == ord('v'):
                config['visualizer'] = not config['visualizer']
                try:
                    settings.save(config)
                except OSError as exc:
                    bridge_error = f'Could not save visualizer setting: {exc}'
                    diagnostics.record('Visualizer preference save failed', exc)
            if pressed == ord('u'):
                updater.check(config, manual=True)
            if current and pressed in (ord('['), ord(']'), ord('0')):
                song = settings.song_key(current)
                adjustment = config['song_offsets'].get(song, 0.0)
                adjustment = 0 if pressed == ord('0') else max(-60, min(60, round(
                    adjustment + (config['offset_step'] if pressed == ord(']') else -config['offset_step']), 3)))
                if adjustment:
                    config['song_offsets'][song] = adjustment
                else:
                    config['song_offsets'].pop(song, None)
                try:
                    settings.save(config)
                except OSError as exc:
                    bridge_error = f'Could not save song timing: {exc}'
                    diagnostics.record('Song timing save failed', exc)
            elif pressed == curses.KEY_DOWN:
                scroll += 1
            elif pressed == curses.KEY_UP:
                scroll = max(0, scroll - 1)
            elif pressed == ord('r') and current and not future and not local:
                future = lyrics_pool.submit(load_lyrics, current, True, config["cache"])
    finally:
        visuals.close()
        updater.close()
        pool.shutdown(wait=False, cancel_futures=True)
        lyrics_pool.shutdown(wait=False, cancel_futures=True)
        if players.bridge:
            players.bridge.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--player', help='Only follow players whose name contains this text')
    parser.add_argument('--list', action='store_true', help='List detected players')
    parser.add_argument('--status', action='store_true', help='Print selected player and song as JSON')
    parser.add_argument('--offset', type=float, default=None, help='Lyric timing adjustment in seconds; positive shows lyrics earlier')
    parser.add_argument('--lrc', help='Use a local LRC, enhanced LRC, or .lyricsfile.yaml file')
    parser.add_argument('--settings', action='store_true', help='Open interactive settings')
    parser.add_argument('--browser-script', action='store_true', help='Export your private browser userscript')
    backup = parser.add_mutually_exclusive_group()
    backup.add_argument('--export-settings', metavar='FILE', help='Export settings to a new JSON file')
    backup.add_argument('--import-settings', metavar='FILE', help='Validate and save settings from JSON')
    parser.add_argument('--logs', action='store_true', help='Read local diagnostics')
    parser.add_argument('--export-report', metavar='FILE', help='Export a private-data-free bug report')
    parser.add_argument('--version', action='version', version=f'tlyrics {updates.VERSION}')
    parser.add_argument('--check-updates', action='store_true', help='Check for a new release')
    parser.add_argument('--update', action='store_true', help='Check and offer to install an update')
    parser.add_argument('--yes', action='store_true', help='Accept --update without prompting')
    args = parser.parse_args()
    if args.yes and not args.update:
        parser.error('--yes requires --update')
    config = settings.load()
    diagnostics.configure(config)
    diagnostics.record('App started', debug=True)
    if args.player:
        config['player'] = args.player
    if args.offset is not None:
        if not math.isfinite(args.offset) or abs(args.offset) > 60:
            parser.error('--offset must be between -60 and 60 seconds')
        config['offset'] = args.offset
    restart = None
    try:
        if args.logs:
            print(diagnostics.read())
            return 0
        if args.export_report:
            print(f'Exported to {diagnostics.export(args.export_report, config)}')
            return 0
        if args.export_settings:
            print(f'Exported to {settings.export_file(args.export_settings, config)}')
            return 0
        if args.import_settings:
            settings.save(settings.import_file(args.import_settings, config))
            print('Settings imported. Restart any running tlyrics instances to apply them.')
            return 0
        if args.check_updates or args.update:
            release = updates.latest()
            if not updates.newer(release):
                print(f'tlyrics {updates.VERSION} is up to date' if release else 'No published releases yet')
                return 0
            print(f'tlyrics {release.version} is available (installed: {updates.VERSION})')
            if args.update:
                approved = args.yes
                if not approved and sys.stdin.isatty():
                    approved = input('Install update? [y/N] ').strip().lower() in ('y', 'yes')
                if approved:
                    updates.install_update(release)
                    print(f'Installed tlyrics {release.version}')
            return 0
        if args.browser_script:
            print(browser_bridge.export_script())
            return 0
        players = Players()
        if args.list or args.status:
            tracks = players.read()
            if args.list:
                for t in tracks:
                    print(clean(f'{t.player}: {t.status} - {t.artist} - {t.title}'))
                if not tracks:
                    print('No supported music players detected.')
            else:
                track = select_player(tracks, config["player"])
                print(json.dumps(vars(track) if track else None, indent=2))
        else:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                parser.error('Run in an interactive terminal, or use --status / --list.')
            with diagnostics.quiet_errors():
                restart = curses.wrapper(ui, players, args, config)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        diagnostics.record('App command failed', exc)
        if config['show_diagnostics']:
            print(f'tlyrics: {exc}', file=sys.stderr)
        return 1
    finally:
        if 'players' in locals():
            try:
                players.close()
            except Exception as exc:
                diagnostics.record('Player shutdown failed', exc)
    if restart:
        try:
            os.execv(str(restart), [str(restart), *sys.argv[1:]])
        except OSError as exc:
            diagnostics.record('Restart failed', exc)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
