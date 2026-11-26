"""Optional CAVA analysis worker and bounded terminal spectrum layouts."""
from collections import deque
import curses
import math
import os
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import rendering

CAPTURE_KEYS = ('viz_backend', 'viz_source', 'viz_bars', 'viz_channels', 'viz_fps',
                'viz_sensitivity', 'viz_autosens', 'viz_noise')


def sources():
    """Only offer output monitors, never microphone sources."""
    if not shutil.which('pactl'):
        return ['auto']
    try:
        result = subprocess.run(['pactl', 'list', 'short', 'sources'], capture_output=True,
                                text=True, timeout=2, check=True)
        return ['auto'] + [parts[1] for line in result.stdout.splitlines()
                           if len(parts := line.split()) > 1 and parts[1].endswith('.monitor')]
    except (OSError, subprocess.SubprocessError):
        return ['auto']


def capture_config(config):
    source = config['viz_source'] if config['viz_backend'] == 'pulse' else 'auto'
    # Values loaded from settings are validated; protect generated INI as well.
    if source != 'auto' and (not source.endswith('.monitor') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-' for c in source)):
        raise ValueError('Choose an output monitor from Visualizer / Audio & response')
    return f'''[general]
framerate = {config['viz_fps']}
bars = {config['viz_bars']}
autosens = {int(config['viz_autosens'])}
sensitivity = {config['viz_sensitivity']}
lower_cutoff_freq = 50
higher_cutoff_freq = 12000
sleep_timer = 0
[input]
method = {config['viz_backend']}
source = {source}
[output]
method = raw
raw_target = /dev/stdout
data_format = binary
bit_format = 8bit
channels = {config['viz_channels']}
[smoothing]
noise_reduction = {config['viz_noise']}
'''


class FrameDecoder:
    def __init__(self, size):
        self.size = size
        self.pending = b''

    def feed(self, payload):
        data = self.pending + payload
        count = len(data) // self.size
        self.pending = data[count * self.size:]
        return tuple(v / 255 for v in data[(count - 1) * self.size:count * self.size]) if count else None


class Visualizer:
    def __init__(self):
        self.signature = None
        self.thread = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = ()
        self.stamp = 0.0
        self.message = ''
        self.levels = []
        self.peaks = []
        self.history = deque(maxlen=20)
        self.last_frame = 0.0
        self.last_history = 0.0

    def configure(self, config):
        signature = tuple(config[k] for k in CAPTURE_KEYS) if config['visualizer'] else None
        if signature == self.signature:
            return
        self.close()
        self.signature = signature
        self.levels, self.peaks = [], []
        self.history.clear()
        with self.lock:
            self.latest, self.stamp, self.message = (), 0.0, ''
        if signature is None:
            return
        if not shutil.which('cava'):
            self.message = 'Install cava to enable the audio visualizer.'
            return
        self.stop = threading.Event()
        self.message = 'Connecting to audio output...'
        self.thread = threading.Thread(target=self._capture, args=(dict(config),), daemon=True)
        self.thread.start()

    def _capture(self, config):
        process = None
        errors = b''
        try:
            with tempfile.TemporaryDirectory(prefix='tlyrics-cava-') as directory:
                path = Path(directory) / 'config'
                path.write_text(capture_config(config))
                process = subprocess.Popen(['cava', '-p', str(path)], stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
                decoder = FrameDecoder(config['viz_bars'])
                started = time.monotonic()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, 'audio')
                    selector.register(process.stderr, selectors.EVENT_READ, 'error')
                    while not self.stop.is_set():
                        for key, _ in selector.select(.1):
                            payload = os.read(key.fileobj.fileno(), 65536)
                            if not payload:
                                selector.unregister(key.fileobj)
                                continue
                            if key.data == 'error':
                                errors = (errors + payload)[-2000:]
                            else:
                                frame = decoder.feed(payload)
                                if frame is not None:
                                    with self.lock:
                                        self.latest, self.stamp, self.message = frame, time.monotonic(), ''
                        if process.poll() is not None:
                            detail = rendering.safe_text(errors.decode(errors='replace')).strip()
                            raise RuntimeError(detail[:150] or 'Audio backend stopped; toggle the visualizer to retry.')
                        with self.lock:
                            if not self.stamp and time.monotonic() - started > 5:
                                self.message = 'No audio frames. Check the output device or audio backend.'
        except (OSError, ValueError, RuntimeError) as exc:
            with self.lock:
                self.message = f'Visualizer: {exc}'
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                process.stderr.close()

    def frame(self, config, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            latest, stamp = self.latest, self.stamp
        target = latest if now - stamp < 1 else (0.0,) * len(latest)
        dt = min(.25, max(0.0, now - self.last_frame)) if self.last_frame else 1 / config['fps']
        self.last_frame = now
        if len(self.levels) != len(target):
            self.levels = [0.0] * len(target)
            self.peaks = [0.0] * len(target)
        response = {'fast': .025, 'balanced': .08, 'soft': .18}[config['viz_smoothing']]
        fall = {'fast': 4.0, 'balanced': 1.8, 'slow': .7}[config['viz_decay']]
        weight = 1 - math.exp(-dt / response)
        for i, value in enumerate(target):
            self.levels[i] += (value - self.levels[i]) * weight
            self.peaks[i] = max(self.levels[i], self.peaks[i] - fall * dt)
        if now - self.last_history >= 1 / config['viz_fps']:
            self.history.append(tuple(self.levels))
            self.last_history = now
        return self.levels

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
            self.thread = None
        self.signature = None


def region(height, config, top=8):
    """Reserve space without covering headers, lyrics, or keyboard hints."""
    if not config['visualizer'] or height < 18:
        return top, height - 3, None
    size = min(config['viz_height'], max(2, (height - top - 5) // 2))
    if config['viz_position'] == 'top':
        return top + size + 1, height - 3, (top, size)
    y = height - 3 - size
    return top, y - 1, (y, size)


def grid(levels, peaks, history, width, height, config):
    """Return sparse cells (row, column) -> intensity, bounded by the panel."""
    cells = {}
    if not levels or width < 1 or height < 1:
        return cells
    def cell(y, x, intensity):
        if 0 <= x < width and 0 <= y < height and intensity > .015:
            cells[y, x] = max(cells.get((y, x), 0), min(1.0, intensity))
    count = min(len(levels), width)
    data = [max(levels[i * len(levels) // count:(i + 1) * len(levels) // count]) for i in range(count)]
    layout = config['viz_layout']
    if layout == 'levels':
        # Reduce bands once per row instead of repainting a row for every band.
        for row in range(min(height, count)):
            start = row * count // min(height, count)
            stop = (row + 1) * count // min(height, count)
            value = max(data[start:stop])
            for col in range(round(value * width)):
                cell(row, col, value)
        return cells
    history_rows = tuple(history)[-height:][::-1] if layout == 'waterfall' else ()
    for i, value in enumerate(data):
        x = i * width // count
        span = max(1, (i + 1) * width // count - x - config['viz_gap'])
        amount = max(0, min(height, math.ceil(value * height))) if value > .015 else 0
        if layout == 'radial':
            angle = i / count * math.tau
            radius = .15 + value * .8
            cell(round((height - 1) / 2 * (1 + math.sin(angle) * radius)),
                 round((width - 1) / 2 * (1 + math.cos(angle) * radius)), value)
        elif layout == 'waterfall':
            for row, frame in enumerate(history_rows):
                if frame:
                    intensity = frame[min(len(frame) - 1, i * len(frame) // count)]
                    for dx in range(span):
                        cell(row, x + dx, intensity)
        else:
            for dx in range(span):
                if layout == 'mirror':
                    half = value * height / 2
                    for row in range(height):
                        if abs(row - (height - 1) / 2) < half:
                            cell(row, x + dx, value)
                elif layout == 'outline':
                    if amount:
                        cell(height - amount, x + dx, value)
                else:
                    for row in range(height - amount, height):
                        cell(row, x + dx, (height - row) / height)
                    if config['viz_peaks'] and peaks:
                        peak = peaks[min(len(peaks) - 1, i * len(peaks) // count)]
                        if peak > .015:
                            cell(max(0, height - math.ceil(peak * height)), x + dx, 1)
    return cells


def draw(screen, engine, config, panel):
    if panel is None:
        return
    y, height = panel
    _, width = screen.getmaxyx()
    columns = max(1, min(256, width - 4, config['viz_width'] or width - 4))
    left = max(1, (width - columns) // 2)
    levels = engine.frame(config)
    if engine.message:
        text = rendering.safe_text(engine.message)[:max(0, width - 4)]
        try:
            screen.addstr(y, 2, text, curses.A_DIM)
        except curses.error:
            pass
        return
    cells = grid(levels, engine.peaks, engine.history, columns, height, config)
    glyph = {'blocks': '█', 'thin': '│', 'dots': '•', 'ascii': '#'}[config['viz_glyph']]
    rich = curses.has_colors() and curses.COLORS >= 256 and curses.COLOR_PAIRS >= 80
    mode = config['viz_colour']
    coloured = curses.has_colors()
    base_attr = curses.color_pair(1) if coloured else curses.A_BOLD
    palette = [curses.color_pair(64 + i) for i in range(8)] if rich and mode == 'palette' else []
    horizontal = config['viz_gradient'] == 'horizontal'
    waterfall = config['viz_layout'] == 'waterfall'
    rows = [[] for _ in range(height)]
    for (row, col), intensity in sorted(cells.items()):
        if mode == 'mono' or not coloured:
            attr = curses.A_BOLD
        elif mode == 'theme' or not rich:
            attr = base_attr
        else:
            phase = col / max(1, columns - 1) if horizontal else intensity
            attr = palette[min(7, int(phase * 7))]
        char = (' .:-=+*#%@'[min(9, int(intensity * 9))] if waterfall else glyph)
        rows[row].append((col, char, attr))
    for row, values in enumerate(rows):
        runs = []
        for col, char, attr in values:
            if runs and runs[-1][0] + len(runs[-1][1]) == col and runs[-1][2] == attr:
                runs[-1][1] += char
            else:
                runs.append([col, char, attr])
        for col, text, attr in runs:
            try:
                screen.addstr(y + row, left + col, text, attr)
            except curses.error:
                pass
