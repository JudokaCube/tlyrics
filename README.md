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
- `c`: calibrate sync. Tap Space as each displayed line starts; use left/right to
  choose a line. After at least three distinct taps, Enter reviews the adjustment
  before saving it for this song. Backspace resets taps; Esc cancels.
- `l`: lyric sources. Check timestamp availability, choose another LRCLIB version,
  load a local lyrics file, or return to automatic lookup.

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
Calibration changes the song offset, leaving the global offset alone. It cannot
create missing word timestamps or fix lyrics matched to a different recording.
Manual source choices are saved per song, including a copy of local lyrics, in
`~/.config/tlyrics/lyrics`. They stay selected until you choose automatic lookup,
even with caching disabled. Source search sends the title and artist to LRCLIB.
**Playback & lyrics / Keep playing line centered** keeps the current line in the
middle, including at the start and end of a song. Turn it off for compact scrolling.

## Appearance and visualizer

Choose presets, colours, backgrounds, borders, layouts, word effects and progress
styles in settings. Advanced options have their own submenus. Gradients need a
256-colour terminal; glow is simulated with colour and brightness.
Resizing reflows lyrics and fits complete visualizer bars. Playback needs at least
24 columns by 14 rows; smaller windows show a resize prompt and keep your place.
Press `p` anywhere in settings for an animated appearance preview. Up/down selects
an option, left/right changes it, and Esc returns. The preview uses sample lyrics
and simulated audio; save or discard changes through the settings menu.
**Visualizer / Apply visualizer preset** offers subtle, smooth and energetic
styles. Presets preserve the selected audio backend/device and enabled state.

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

## Diagnostics

Playback hides technical status and error details by default. In **Settings /
Diagnostics**, enable **Show technical details** when troubleshooting, view local
logs, export a bug report, or clear the logs. Logging can be **off**, **errors**
(default), or **debug**. Keep 2, 3 (default), or 7 files, capped at 256 KiB each.
Debug adds lifecycle events. Turning logging off stops new entries; use Clear to
delete existing entries. Diagnostics stay in `~/.local/state/tlyrics`
(`XDG_STATE_HOME` is supported) and are never uploaded automatically.

```sh
tlyrics --logs
tlyrics --export-report ~/Downloads/tlyrics-report.json
```

Reports contain versions, display/playback preferences, and error types with code
locations. Lyrics, song history, browser tokens, local paths and exception messages
are excluded. Export requires a new filename. Review the report before sharing it.
