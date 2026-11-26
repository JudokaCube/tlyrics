# tlyrics - Terminal Lyrics

Synced lyrics and an optional audio visualizer in your Linux terminal.

## Install

Requires Python 3.10+ and Python D-Bus (`python-dbus` on Arch,
`python3-dbus` on Debian/Ubuntu/Fedora). Debian/Ubuntu may also need `python3-venv`.
The installer handles Python dependencies in a private environment.

```sh
git clone https://github.com/JudokaCube/tlyrics.git
cd tlyrics
sh install.sh
tlyrics
```

## Controls

- `s`: settings. Enter opens options; Esc goes back and saves when leaving settings.
- `Ctrl+S`: save and return. **Discard and return** cancels pending edits.
- `v`: toggle the visualizer. `q` or Esc from playback: quit.
- `[` / `]`: adjust this song's timing. `0`: reset. `r`: retry lyrics.
- `u`: check for updates. Arrow keys scroll untimed lyrics.

`tlyrics --settings` opens settings directly. `--list` lists players;
`--player spotify` selects one. `--lrc song.lrc` loads local line/word-timed lyrics
(LRC, enhanced LRC, or `.lyricsfile.yaml`).

## Players and lyrics

Spotify, LiMusic, Pear Desktop, VLC, mpv and other recognised MPRIS music apps
are supported. Browser sessions must expose a supported music-site URL.
For Spotify or YouTube Music web, enable **Browser bridge** in settings and export
the browser script. Install that `.user.js` file in Violentmonkey or Tampermonkey,
then reload the website. Keep it private: it contains your local bridge token.
After a bridge update, export and reinstall the script.

Lyrics come from [LRCLIB](https://lrclib.net); song metadata is sent there for lookup.
Word sync needs actual word timestamps. Songs without them stay line-synced, even
with word sync enabled. Timing also depends on the recording and lyric source.
Positive timing offsets show lyrics earlier. Settings include a global offset and
saved per-song adjustments. Optional PyGObject enables immediate MPRIS seek/pause signals.
**Playback & lyrics / Keep playing line centered** keeps the current line in the
middle, including at the start and end of a song. Turn it off for compact scrolling.

## Appearance and visualizer

Choose presets, colours, backgrounds, borders, layouts, word effects and progress
styles in settings. Advanced options have their own submenus. Gradients need a
256-colour terminal; glow is simulated with colour and brightness.
Resizing reflows lyrics and fits complete visualizer bars. Playback needs at least
24 columns by 14 rows; smaller windows show a resize prompt and keep your place.

**No CAVA required.** tlyrics performs its own FFT spectrum analysis using NumPy.
Audio comes from your system output through `parec` (PulseAudio) or `pw-record`
(PipeWire). Install your audio service's command-line tools if they are missing,
then select the backend in **Visualizer / Audio & response**.

Six layouts: bars, mirror, outline, waterfall, radial and horizontal levels.
Adjust placement, size, bands, mono/stereo, sensitivity, noise reduction, smoothing,
peak decay, colours, glyphs and frame rate. The visualizer is off by default.
Floating peak markers are off by default. Enable them under **Visualizer /
Style & colours** if you want marks showing recent peaks above the bars.
It includes other apps using the same output, does not capture your microphone,
and never saves or uploads audio. Capture pauses below 18 terminal rows.

## Settings and updates

Preferences survive upgrades and are saved in `~/.config/tlyrics`, with a recovery
backup. Cache lives in `~/.cache/tlyrics`. XDG paths are supported.

Use **Settings backup** to export/import preferences and song timing, or:

```sh
tlyrics --export-settings ~/Downloads/tlyrics-settings.json
tlyrics --import-settings ~/Downloads/tlyrics-settings.json
```

Exports need a new filename. Imports are validated before saving. Command aliases
and private browser tokens stay local. Aliases can be managed in settings and
apply immediately; existing commands are never overwritten.

The app checks daily and asks before updating. Settings offer **ask**, **auto** or
**off**, plus a check interval. `--version` shows your version; `--check-updates`
checks from the command line. `--update` offers to install, and `--update --yes`
accepts automatically. Accepted in-app updates restart the app and keep preferences.
