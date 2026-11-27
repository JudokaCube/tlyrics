"""Calibration and deliberate per-song lyric selections."""
import bisect
import math
import json
from pathlib import Path
import statistics

import lyrics
import settings


class Calibration:
    def __init__(self, doc, position, offset):
        landmarks = [lyrics.Line(line.words[0].start if line.words else line.start, line.text)
                     for line in doc.lines if line.text.strip()]
        self.lines = sorted({line.start: line for line in landmarks}.values(), key=lambda line: line.start)
        if not self.lines:
            raise ValueError('Calibration needs timed lyrics.')
        self.index = min(len(self.lines) - 1, bisect.bisect_left([line.start for line in self.lines], position + offset + 1))
        self.samples = {}
        self.ready = True

    def prepare(self, position, offset):
        # Leave time to read the target before vocals begin.
        index = bisect.bisect_left([line.start for line in self.lines], position + offset + 5)
        if index == len(self.lines):
            return False
        self.index = index
        self.ready = True
        return True

    def move(self, delta):
        self.index = max(0, min(len(self.lines) - 1, self.index + delta))

    def tap(self, position):
        if position is None or not math.isfinite(position):
            raise ValueError('The player clock is unavailable.')
        value = self.lines[self.index].start - position
        if abs(value) > 60:
            raise ValueError('Choose a line closer to the current vocals.')
        self.samples[self.index] = value
        self.move(1)

    def adjustment(self, global_offset):
        if len(self.samples) < 3:
            raise ValueError('Tap at least three different lines before saving.')
        values = list(self.samples.values())
        middle = statistics.median(values)
        consistent = [value for value in values if abs(value - middle) <= .35]
        if len(consistent) < 3 or len(consistent) < len(values) * .6:
            raise ValueError('Taps disagree. Press Backspace to reset and try again.')
        result = round(statistics.median(consistent) - global_offset, 3)
        if abs(result) > 60:
            raise ValueError('Adjustment exceeds the supported range.')
        return result


def selection_path(track):
    return settings.CONFIG.parent / 'lyrics' / (settings.song_key(track) + '.json')


def selected(track):
    try:
        return settings.read_json(selection_path(track))
    except FileNotFoundError:
        return None


def select(track, record):
    if len((json.dumps(record, indent=2, allow_nan=False) + '\n').encode()) > 4 * 1024 * 1024:
        raise ValueError('Lyrics selection is too large to save.')
    doc = lyrics.from_record(record)
    if not doc.lines and not doc.plain and not record.get('instrumental'):
        raise ValueError('This source has no usable lyrics.')
    settings.atomic_write(selection_path(track), record)
    return doc


def automatic(track):
    selection_path(track).unlink(missing_ok=True)


def local_record(path):
    path = Path(path).expanduser()
    with path.open('rb') as handle:
        raw = handle.read(lyrics.MAX_BYTES + 1)
    if len(raw) > lyrics.MAX_BYTES:
        raise ValueError('Lyrics file is too large.')
    field = 'lyricsfile' if path.suffix.lower() in ('.yaml', '.yml') else 'syncedLyrics'
    return {field: raw.decode('utf-8-sig'), '_tlyrics_source': 'Local file'}


def source_summary(record, doc):
    source = record.get('_tlyrics_source', 'LRCLIB automatic')
    timed = sum(bool(line.words) for line in doc.lines)
    timing = f'Word timestamps: {timed}/{len(doc.lines)} lines' if timed else (
        'Line timestamps only' if doc.lines else 'Instrumental' if record.get('instrumental') else
        'Untimed lyrics' if doc.plain else 'No lyrics loaded')
    return f'{timing} | {source}'
