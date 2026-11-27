"""Recognised music services and desktop players; browser titles are not identity."""
from urllib.parse import urlparse

BROWSERS = ('firefox', 'chromium', 'chrome', 'brave', 'vivaldi', 'edge', 'zen',
            'librewolf', 'floorp', 'waterfox', 'opera', 'epiphany')
WEB_SERVICES = {
    'open.spotify.com': 'spotify-web',
    'music.youtube.com': 'youtube-music-web',
    'music.apple.com': 'apple-music-web',
    'soundcloud.com': 'soundcloud-web',
    'listen.tidal.com': 'tidal-web',
    'deezer.com': 'deezer-web',
    'music.amazon.com': 'amazon-music-web',
    'music.amazon.co.uk': 'amazon-music-web',
    'play.qobuz.com': 'qobuz-web',
    'pandora.com': 'pandora-web',
    'bandcamp.com': 'bandcamp-web',
}
DESKTOP = {
    'spotify': ('spotify',),
    'youtube-music': ('pear-desktop', 'pear_desktop', 'peardesktop', 'youtubemusic', 'youtube-music', 'ytmusic'),
    'limusic': ('limusic',),
    'tmusic': ('tmusic',),
    'apple-music': ('cider', 'applemusic', 'apple-music'),
    'tidal': ('tidal', 'tidal-hifi', 'tidal_hifi'),
    'deezer': ('deezer',),
    'vlc': ('vlc',), 'mpv': ('mpv',), 'rhythmbox': ('rhythmbox',),
    'strawberry': ('strawberry',), 'clementine': ('clementine',), 'elisa': ('elisa',),
    'audacious': ('audacious',), 'amarok': ('amarok',), 'lollypop': ('lollypop',),
    'tauon': ('tauon', 'tauonmb'), 'deadbeef': ('deadbeef',), 'amberol': ('amberol',),
    'fooyin': ('fooyin',), 'nuclear': ('nuclear',), 'quodlibet': ('quodlibet',),
    'musique': ('musique',), 'g4music': ('g4music',), 'gapless': ('gapless',),
    'sayonara': ('sayonara',), 'cmus': ('cmus',), 'ncspot': ('ncspot',),
    'spotifyd': ('spotifyd',), 'spot': ('spot',), 'harmonoid': ('harmonoid',),
}


def matches_player(name, alias):
    return name == alias or name.startswith(alias + '.') or name.startswith(alias + '-')


def classify(name, url=''):
    name = name.lower().removeprefix('org.mpris.mediaplayer2.')
    # A native app's identity takes priority over the URL of the audio it plays.
    for service, names in DESKTOP.items():
        if any(matches_player(name, alias) for alias in names):
            return service
    if not any(matches_player(name, browser) for browser in BROWSERS):
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower().removeprefix('www.')
    except ValueError:
        return None
    if parsed.scheme not in ('https', 'http'):
        return None
    if host.endswith('.bandcamp.com'):
        return 'bandcamp-web'
    return WEB_SERVICES.get(host)


def accepted(track):
    if track.service in WEB_SERVICES.values():
        return True
    return track.service in DESKTOP or classify(track.player) is not None
