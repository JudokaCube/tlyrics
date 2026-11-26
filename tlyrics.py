#!/usr/bin/python3
"""Terminal Lyrics — synced lyrics for desktop and browser music players."""
import argparse
import bisect
import concurrent.futures
import curses
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
import settings
import browser_bridge

PREFIX = 'org.mpris.MediaPlayer2.'
IFACE = PREFIX + 'Player'
PATH = '/org/mpris/MediaPlayer2'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'tlyrics'


def parse_lrc(text):
    offset = re.search(r'\[offset:([+-]?\d+)\]', text, re.I)
    shift = int(offset[1]) / 1000 if offset else 0
    lines = []
    for line in text.splitlines():
        stamps = re.findall(r'\[(\d+):(\d+(?:\.\d+)?)\]', line)
        words = re.sub(r'\[[^\]]*\]', '', line).strip()
        for minutes, seconds in stamps:
            lines.append((int(minutes) * 60 + float(seconds) + shift, words))
    return sorted(lines, key=lambda item: item[0])


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
        self.bus = dbus.SessionBus()
        self.bridge = None

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
                position = None
                try:
                    position = props.Get(IFACE, 'Position', timeout=0.5)
                except self.dbus.DBusException:
                    pass
                sampled = time.monotonic()
                url = str(m.get('xesam:url', ''))
                service = classify(str(name), url)
                tracks.append(Track(str(name)[len(PREFIX):], str(m.get('xesam:title', '')),
                                    ', '.join(map(str, m.get('xesam:artist', []))),
                                    str(m.get('xesam:album', '')), float(m.get('mpris:length', 0)) / 1e6,
                                    None if position is None else float(position) / 1e6,
                                    str(p.get('PlaybackStatus', 'Stopped')), float(p.get('Rate', 1)), sampled, service))
            except self.dbus.DBusException:
                continue
        bridge = self.bridge
        if bridge:
            for data, stamp in bridge.read():
                tracks.append(Track(data['service'] + '.' + data['session'][:8], data['title'],
                                    data['artist'], data['album'], data['duration'], data['position'],
                                    data['status'], data['rate'], stamp, data['service']))
        return tracks


def classify(name, url=''):
    name = name.lower()
    if 'open.spotify.com' in url:
        return 'spotify-web'
    if 'music.youtube.com' in url:
        return 'youtube-music-web'
    if 'spotify' in name:
        return 'spotify'
    if any(part in name for part in ('pear', 'youtube', 'ytmusic')):
        return 'youtube-music'
    if any(part in name for part in ('firefox', 'chrom', 'brave', 'vivaldi', 'edge', 'zen')):
        return 'browser'
    return name.removeprefix(PREFIX.lower())


def select_player(tracks, preferred=None, previous=None):
    if preferred and preferred != 'auto':
        groups = {'spotify': ('spotify', 'spotify-web'),
                  'youtube-music': ('youtube-music', 'youtube-music-web'),
                  'browser': ('browser', 'spotify-web', 'youtube-music-web')}
        tracks = [t for t in tracks if t.service in groups.get(preferred, (preferred,))
                  or preferred.lower() in t.player.lower()]
    return max(tracks, key=lambda t: (t.status == 'Playing', bool(t.title), bool(t.artist),
                                     t.service.endswith('-web'), t.player == previous,
                                     t.status == 'Paused'), default=None)


def get_lyrics(track, refresh=False, cache_enabled=True):
    digest = hashlib.sha256(json.dumps(track.key).encode()).hexdigest()
    cache = CACHE / (digest + '.json')
    if not refresh and cache_enabled:
        try:
            data = json.loads(cache.read_text())
            if valid_lyrics(data):
                return data
        except (OSError, ValueError):
            pass
    params = {'track_name': track.title, 'artist_name': track.artist}
    if track.album:
        params['album_name'] = track.album
    if 1 <= track.duration <= 3600:
        params['duration'] = str(round(track.duration))
    url = 'https://lrclib.net/api/get?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'tlyrics/0.1 (Linux terminal lyrics)'})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {'message': 'No matching lyrics found. Press r to retry, or use --lrc FILE.'}
        return {'message': f'Lyrics service returned HTTP {exc.code}. Press r to retry.'}
    except (OSError, ValueError) as exc:
        return {'message': f'Lyrics unavailable: {exc}. Press r to retry.'}
    if not valid_lyrics(data):
        return {'message': 'Invalid lyrics response. Press r to retry.'}
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
    return isinstance(data, dict) and all(data.get(key) is None or isinstance(data[key], str)
                                         for key in ('syncedLyrics', 'plainLyrics'))


def clean(text):
    return ''.join(c for c in str(text) if c.isprintable())


def put(screen, y, text, attr=0, centered=False):
    h, w = screen.getmaxyx()
    if 0 <= y < h and w > 2:
        text = clean(text)[:w - 3]
        x = max(1, (w - len(text)) // 2) if centered else 1
        try:
            screen.addnstr(y, x, text, w - x - 1, attr)
        except curses.error:
            pass


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}:{seconds % 60:02d}'


def apply_theme(config):
    if not curses.has_colors():
        return curses.A_BOLD
    colors = dict(cyan=curses.COLOR_CYAN, green=curses.COLOR_GREEN, purple=curses.COLOR_MAGENTA,
                  amber=curses.COLOR_YELLOW, white=curses.COLOR_WHITE)
    curses.start_color()
    try:
        curses.use_default_colors()
        background = -1
    except curses.error:
        background = curses.COLOR_BLACK
    curses.init_pair(1, colors[config['theme']], background)
    return curses.color_pair(1) | curses.A_BOLD


def prompt(screen, label):
    value = ''
    while True:
        screen.erase()
        put(screen, 2, label, curses.A_BOLD)
        put(screen, 4, value + '▏')
        put(screen, 6, 'Enter confirms · Esc cancels', curses.A_DIM)
        screen.refresh()
        key = screen.get_wch()
        if key == '\x1b':
            return None
        if key in ('\n', '\r'):
            return value.strip()
        if key in (curses.KEY_BACKSPACE, '\x7f', '\b'):
            value = value[:-1]
        elif isinstance(key, str) and key.isprintable() and len(value) < 100:
            value += key


def choose(screen, title, values, current):
    index = values.index(current) if current in values else 0
    while True:
        screen.erase()
        put(screen, 1, title, curses.A_BOLD)
        room = max(1, screen.getmaxyx()[0] - 6)
        start = max(0, index - room + 1)
        for row, value in enumerate(values[start:start + room]):
            put(screen, row + 3, str(value), curses.A_REVERSE if row + start == index else 0)
        put(screen, screen.getmaxyx()[0] - 2, '↑ / ↓ choose · Enter select · Esc back', curses.A_DIM)
        screen.refresh()
        key = screen.getch()
        if key == 27:
            return current
        if key in (10, 13):
            return values[index]
        if key == curses.KEY_UP:
            index = (index - 1) % len(values)
        elif key == curses.KEY_DOWN:
            index = (index + 1) % len(values)


def menu(screen, config, tracks):
    draft = dict(config, aliases=list(config['aliases']))
    rows = [('player', 'Player'), ('theme', 'Theme'), ('layout', 'Layout'), ('align', 'Alignment'),
            ('progress', 'Progress bar'), ('timestamps', 'Lyric timestamps'), ('spacing', 'Line spacing'),
            ('offset', 'Timing offset (seconds)'), ('interval', 'Playback refresh (seconds)'),
            ('cache', 'Cache lyrics'), ('bridge', 'Browser bridge'), ('browser', 'Export browser setup script'),
            ('add', 'Add terminal alias'), ('remove', 'Remove terminal alias'), ('save', 'Save and return')]
    index = 0
    message = 'Changes save with Save / Ctrl+S. Alias changes apply immediately.'
    screen.timeout(-1)
    try:
        while True:
            accent = apply_theme(draft)
            screen.erase()
            h, w = screen.getmaxyx()
            put(screen, 1, 'Terminal Lyrics · Settings', accent)
            room = max(1, h - 7)
            start = max(0, index - room + 1)
            for row, (key, label) in enumerate(rows[start:start + room]):
                value = draft.get(key, '')
                if isinstance(value, bool):
                    value = 'on' if value else 'off'
                put(screen, 3 + row, f'{label:<29} {value}', curses.A_REVERSE if row + start == index else 0)
            put(screen, h - 3, message, curses.A_DIM)
            put(screen, h - 2, '↑ / ↓ move · Enter edit · Ctrl+S save · Esc cancel', curses.A_DIM)
            screen.refresh()
            pressed = screen.getch()
            if pressed == 27:
                return
            if pressed == curses.KEY_UP:
                index = (index - 1) % len(rows)
            elif pressed == curses.KEY_DOWN:
                index = (index + 1) % len(rows)
            elif pressed in (10, 13, 19):
                key = 'save' if pressed == 19 else rows[index][0]
                try:
                    if key == 'save':
                        settings.save(draft)
                        config.update(draft)
                        return
                    if key == 'player':
                        values = list(dict.fromkeys(['auto', 'spotify', 'youtube-music', 'browser', 'spotify-web',
                                                     'youtube-music-web', 'limusic', draft['player']] + [t.player for t in tracks]))
                        draft[key] = choose(screen, 'Player (browser fallback: choose browser)', values, draft[key])
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
                    message = str(exc)
    finally:
        screen.timeout(80)
        apply_theme(config)


def ui(screen, players, args, config):
    curses.raw()  # Let Ctrl+S reach the menu instead of terminal flow control.
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.timeout(80)
    accent = apply_theme(config)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    lyrics_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    current = None
    key = None
    data = {}
    lines = []
    future = None
    polling = None
    next_poll = 0
    offset = config["offset"]
    scroll = 0
    error = ''
    available = []
    local = {'syncedLyrics': Path(args.lrc).read_text()} if args.lrc else None
    if args.settings:
        menu(screen, config, [])
        offset = config['offset']
        accent = apply_theme(config)
    try:
        while True:
            now = time.monotonic()
            if config['bridge'] and players.bridge is None:
                try:
                    players.bridge = browser_bridge.Bridge()
                except OSError as exc:
                    config['bridge'] = False
                    error = f'Browser bridge: {exc}'
            elif not config['bridge'] and players.bridge:
                players.bridge.close()
                players.bridge = None
            if polling is None and now >= next_poll:
                polling = pool.submit(players.read)
            if polling is not None and polling.done():
                try:
                    available = polling.result()
                    current = select_player(available, config["player"],
                                            current.player if current else None)
                    error = ''
                except Exception as exc:
                    current = None
                    error = str(exc)
                polling = None
                next_poll = now + config["interval"]
            new_key = current.key if current and current.title else None
            if new_key != key:
                key = new_key
                data, lines, scroll = {}, [], 0
                if future:
                    future.cancel()
                future = None
                if key:
                    if local:
                        data = local
                        lines = parse_lrc(data['syncedLyrics'])
                    else:
                        future = lyrics_pool.submit(get_lyrics, current, False, config["cache"])
            if future and future.done():
                try:
                    data = future.result()
                except Exception as exc:
                    data = {'message': f'Lyrics unavailable: {exc}'}
                lines = parse_lrc(data.get('syncedLyrics') or '')
                future = None
            screen.erase()
            h, w = screen.getmaxyx()
            put(screen, 0, 'tlyrics', accent)
            if current and current.title:
                put(screen, 2, current.title, curses.A_BOLD, True)
                put(screen, 3, current.artist, curses.A_DIM, True)
                position = current.seconds()
                stamp = clock(position) if position is not None else '--:--'
                put(screen, 5, f'{current.player} · {current.status} · {stamp} / {clock(current.duration)} · offset {offset:+.1f}s', centered=True)
                if config["progress"] and position is not None and current.duration:
                    length = max(1, w - 6)
                    filled = min(length, max(0, int(length * position / current.duration)))
                    put(screen, 6, '━' * filled + '─' * (length - filled), accent)
                room = max(1, (h - 11) // config["spacing"])
                if lines and position is not None:
                    index = active_line(lines, position + offset)
                    count = 1 if config['layout'] == 'focus' else min(3, room) if config['layout'] == 'compact' else room
                    start = max(0, index - count // 2)
                    for row, (timestamp, words) in enumerate(lines[start:start + count]):
                        selected = start + row == index
                        prefix = (clock(timestamp) + '  ') if config['timestamps'] else ''
                        y = 8 + row * config['spacing'] if config['layout'] == 'scroll' else max(8, h // 2 - count // 2 + row)
                        put(screen, y, ('› ' if selected else '  ') + prefix + (words or '♪'),
                            accent if selected else curses.A_DIM, config['align'] == 'center')
                elif future:
                    put(screen, h // 2, 'Finding lyrics…', centered=True)
                else:
                    plain = data.get('plainLyrics') or ''
                    message = data.get('message') or ('Instrumental ♪' if data.get('instrumental') else
                              'Player does not report position; sync unavailable.' if lines else
                              'Only unsynced lyrics available. Use ↑ / ↓ to scroll.' if plain else 'No lyrics available.')
                    put(screen, 7, message, curses.A_DIM, True)
                    display = [words for _, words in lines] if lines else plain.splitlines()
                    scroll = min(scroll, max(0, len(display) - room))
                    for row, words in enumerate(display[scroll:scroll + room]):
                        put(screen, 8 + row * config['spacing'], words, centered=config['align'] == 'center')
            else:
                put(screen, h // 2, error or 'Waiting for a song… Start an MPRIS-enabled music player.', centered=True)
            put(screen, h - 2, 'q quit   s settings   [ / ] timing   0 reset   r retry   ↑ / ↓ scroll', curses.A_DIM, True)
            screen.refresh()
            pressed = screen.getch()
            if pressed in (ord('q'), 27, 3):
                break
            if pressed == ord('s'):
                config['offset'] = offset
                menu(screen, config, available)
                offset = config['offset']
                accent = apply_theme(config)
                next_poll = 0
            if pressed == ord('['):
                offset = max(-60, round(offset - 0.2, 1))
            elif pressed == ord(']'):
                offset = min(60, round(offset + 0.2, 1))
            elif pressed == ord('0'):
                offset = 0
            elif pressed == curses.KEY_DOWN:
                scroll += 1
            elif pressed == curses.KEY_UP:
                scroll = max(0, scroll - 1)
            elif pressed == ord('r') and current and not future and not local:
                future = lyrics_pool.submit(get_lyrics, current, True, config["cache"])
    finally:
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
    parser.add_argument('--lrc', help='Use a local timed .lrc file instead of fetching lyrics')
    parser.add_argument('--settings', action='store_true', help='Open interactive settings')
    parser.add_argument('--browser-script', action='store_true', help='Export your private browser userscript')
    args = parser.parse_args()
    config = settings.load()
    if args.player:
        config['player'] = args.player
    if args.offset is not None:
        if not __import__('math').isfinite(args.offset) or abs(args.offset) > 60:
            parser.error('--offset must be between -60 and 60 seconds')
        config['offset'] = args.offset
    try:
        if args.browser_script:
            print(browser_bridge.export_script())
            return 0
        players = Players()
        if args.list or args.status:
            tracks = players.read()
            if args.list:
                for t in tracks:
                    print(clean(f'{t.player}: {t.status} — {t.artist} — {t.title}'))
                if not tracks:
                    print('No MPRIS players detected.')
            else:
                track = select_player(tracks, config["player"])
                print(json.dumps(vars(track) if track else None, indent=2))
        else:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                parser.error('Run in an interactive terminal, or use --status / --list.')
            curses.wrapper(ui, players, args, config)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f'tlyrics: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
