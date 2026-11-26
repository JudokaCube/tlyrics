# tlyrics - Terminal Lyrics

Synced lyrics in your Linux terminal. Follows the song, pauses and seeks.

## Install

Requires Python 3.10+ and Python D-Bus (`python-dbus` on Arch,
`python3-dbus` on Debian/Ubuntu/Fedora).
The installer adds PyYAML in a private environment if it isn't installed already.
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
brightness. Gradients need a 256-colour terminal and fall back on simpler terminals.

Spotify desktop, LiMusic, Pear Desktop and other MPRIS players work automatically.
Enable MPRIS in your desktop player if needed. Spotify and YouTube Music websites
can use your browser's media integration; select **browser** if the service name
isn't exposed. Only the browser's active media session is visible through MPRIS.

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
media timestamps from whole-second browser labels. Sync still depends on the
recording and lyric timestamps. Positive timing adjustments show lyrics earlier;
use global offset for audio-device latency and song adjustments for individual tracks.
With PyGObject installed (`python-gobject` on Arch, `python3-gi` on Debian/Ubuntu),
MPRIS pause/seek notifications also trigger immediate refreshes; polling works without it.

Settings: `~/.config/tlyrics`. Cache: `~/.cache/tlyrics` (XDG paths supported).
Aliases are commands in `~/.local/bin`; existing commands are never overwritten.

Tests (with PyYAML installed): `python3 -m unittest discover -s tests -v`.
