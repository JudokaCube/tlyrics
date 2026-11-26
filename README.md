# tlyrics - Terminal Lyrics

Synced lyrics in your Linux terminal. Follows the song, pauses and seeks.

## Install

Requires Python 3.10+ and Python D-Bus (`python-dbus` on Arch,
`python3-dbus` on Debian/Ubuntu/Fedora).

```sh
git clone https://github.com/JudokaCube/tlyrics.git
cd tlyrics
sh install.sh
tlyrics
```

## Use

- `s`: settings - player, theme, layout, spacing, timing, cache and command aliases.
- `[` / `]`: adjust lyric timing. `0`: reset. `r`: retry. `q`: quit.
- `tlyrics --settings`: open settings directly.
- `tlyrics --list`: list players. `tlyrics --player spotify`: select one.
- `tlyrics --lrc song.lrc`: use your own timed lyrics.

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

Lyrics come from [LRCLIB](https://lrclib.net). Track metadata is sent there for
lookup; lyrics are cached locally. Missing timed lyrics are shown without sync.
The song clock follows playback seconds, independently of lyric line timestamps.
Sync quality depends on player timing and the available lyrics.

Settings: `~/.config/tlyrics`. Cache: `~/.cache/tlyrics` (XDG paths supported).
Aliases are commands in `~/.local/bin`; existing commands are never overwritten.

Tests: `python3 -m unittest discover -s tests -v`.
