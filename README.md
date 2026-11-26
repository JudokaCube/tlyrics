# tlyrics - Terminal Lyrics

Synced lyrics in your Linux terminal. Follows the song, pauses and seeks.

## Install

Requires Python 3.10+ and Python D-Bus (`python-dbus` on Arch,
`python3-dbus` on Debian/Ubuntu/Fedora).
The installer adds PyYAML in a private environment.
Debian/Ubuntu may also need `python3-venv`.

```sh
git clone https://github.com/JudokaCube/tlyrics.git
cd tlyrics
sh install.sh
tlyrics
```

## Use

- `s`: settings - player, theme, layout, effects, timing, cache and command aliases.
- `[` / `]`: adjust this song by 0.05s (saved). `0`: reset this song. `r`: retry. `q`: quit.
- `tlyrics --settings`: open settings directly.
- `tlyrics --list`: list players. `tlyrics --player spotify`: select one.
- `tlyrics --lrc song.lrc`: use LRC, enhanced word-timed LRC, or `.lyricsfile.yaml`.

Word highlighting uses real word timestamps from LRCLIB or local files. Line-only
lyrics stay line-synced; word times are never guessed. Settings include whole-word
or sweep highlighting, word/line gradients, soft/bright glow, pulse, animation,
width, and frame rate. Effects are optional; glow is simulated with colour and
brightness, without background blocks. Completed words stay readable; upcoming
words are dimmed. Gradients need a 256-colour terminal and fall back on simpler terminals.

Spotify desktop, LiMusic, Pear Desktop, VLC, mpv and recognised music apps are
supported through MPRIS. Enable MPRIS in your player if needed. Browser sessions
must expose a music-site URL: Spotify, YouTube Music, Apple Music, SoundCloud,
Tidal, Deezer, Amazon Music, Qobuz, Pandora or Bandcamp. WhatsApp, general video
sites and unidentified browser sessions are ignored. If your browser doesn't
expose the site URL, use the Spotify/YouTube Music bridge below.

For direct web-player detection/timing, enable **Browser bridge** in settings and
export the browser script. Install that generated `.user.js` file in a userscript
manager such as Violentmonkey or Tampermonkey, then reload Spotify/YouTube Music.
`tlyrics --browser-script` also prints its path. Keep the generated file private:
it contains your local bridge token. The bridge runs only while tlyrics is open.
Website changes can require a bridge update; background-tab throttling can delay it.
After upgrading tlyrics, export and reinstall the browser script to get timing fixes.

Lyrics come from [LRCLIB](https://lrclib.net). Track metadata is sent there for
lookup; lyrics are cached locally. Missing timed lyrics are shown without sync.
The song clock follows playback seconds, independently of lyric line timestamps.
The clock smooths measurement jitter, corrects seeks, and distinguishes precise
media timestamps from whole-second browser labels. Players such as LiMusic
publish cached positions every few seconds; the clock continues between those
updates while playback remains active. Sync still depends on the
recording and lyric timestamps. Positive timing adjustments show lyrics earlier;
use global offset for audio-device latency and song adjustments for individual tracks.
With PyGObject installed (`python-gobject` on Arch, `python3-gi` on Debian/Ubuntu),
MPRIS pause/seek notifications also trigger immediate refreshes; polling works without it.

Settings: `~/.config/tlyrics`. Cache: `~/.cache/tlyrics` (XDG paths supported).
Aliases are commands in `~/.local/bin`; existing commands are never overwritten.

Renderer benchmark: `python3 tests/benchmark_render.py` (synthetic, excludes terminal I/O).

Tests (with PyYAML installed): `python3 -m unittest discover -s tests -v`.

## Updates

Version: 1.0.1. `tlyrics --version` shows your installed version.
By default, the app checks GitHub daily and asks before installing a new release.
Choose **auto**, **ask** or **off** in settings, with a 6-hour, daily or weekly
check interval. Press `u` or choose **Check for updates now** for a manual check.
Accepted updates install in the background and restart the app. Settings,
aliases and cached lyrics stay intact. Failed checks do not interrupt playback.

`tlyrics --check-updates` checks from the command line. `tlyrics --update`
asks to install; add `--yes` to accept automatically. Run `sh install.sh` once
to enable updates for older installations. Source checkouts are never overwritten.
Packages are downloaded from this repository, checked against SHA256SUMS and
validated before activation. Previous releases are kept in the installation folder.

To publish: change `VERSION`, run the tests and `python3 build_release.py`, then
attach both files in `dist/` to a GitHub release tagged `v<version>`.
