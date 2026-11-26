"""Native spectrum analysis worker and bounded terminal spectrum layouts."""
from collections import deque
import curses
import math
import os
import selectors
import shutil
import subprocess
import threading
import time

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


SAMPLE_RATE = 48000
FFT_SIZE = 2048
PCM_FRAME_BYTES = 4  # signed native-endian int16, left + right


def capture_command(config):
    """Audio tools only transport PCM; tlyrics does all spectrum analysis."""
    source = config['viz_source']
    if source != 'auto' and (not source.endswith('.monitor') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-' for c in source)):
        raise ValueError('Choose an output monitor from Visualizer / Audio & response')
    if config['viz_backend'] == 'pipewire':
        return ['pw-record', '--raw', '--format=s16', '--rate=48000', '--channels=2',
                '--latency=20ms', '--target=auto', '--properties={ stream.capture.sink=true stream.monitor=true }', '-']
    return ['parec', '--raw', '--format=s16ne', '--rate=48000', '--channels=2',
            '--latency-msec=20', '--process-time-msec=10', '--client-name=tlyrics',
            '--device=' + ('@DEFAULT_MONITOR@' if source == 'auto' else source)]


class PCMBuffer:
    """Bounded sliding FFT window; retain byte alignment across fragmented reads."""
    def __init__(self):
        self.pending = b''
        self.window = b''
        self.generation = 0

    def feed(self, payload):
        data = self.pending + payload
        end = len(data) // PCM_FRAME_BYTES * PCM_FRAME_BYTES
        self.pending = data[end:]
        if end:
            self.window = (self.window + data[:end])[-FFT_SIZE * PCM_FRAME_BYTES:]
            self.generation += 1
        return self.window if len(self.window) == FFT_SIZE * PCM_FRAME_BYTES else None


class Spectrum:
    """Hann-windowed FFT, logarithmic bands, noise gate and time-based gain."""
    def __init__(self, config):
        import numpy as np
        self.np = np
        self.config = config
        self.window = np.hanning(FFT_SIZE)[:, None]
        self.scale = 2 / self.window.sum()
        self.count = config['viz_bars'] // (2 if config['viz_channels'] == 'stereo' else 1)
        edges = np.geomspace(45, 16000, self.count + 1) * FFT_SIZE / SAMPLE_RATE
        self.left = np.clip(np.floor(edges[:-1]).astype(int), 1, FFT_SIZE // 2)
        self.right = np.minimum(FFT_SIZE // 2 + 1, np.maximum(self.left + 1, np.ceil(edges[1:]).astype(int)))
        self.reference = .08
        self.last = None

    def analyze(self, payload, now):
        np = self.np
        samples = np.frombuffer(payload, dtype='=i2').reshape(FFT_SIZE, 2).astype(np.float64) / 32768
        samples -= samples.mean(axis=0)
        magnitude = np.abs(np.fft.rfft(samples * self.window, axis=0)) * self.scale
        power = magnitude ** 2
        summed = np.concatenate((np.zeros((1, 2)), np.cumsum(power, axis=0)))
        bands = np.sqrt(np.maximum(0, summed[self.right] - summed[self.left]))
        # Average channel energies for mono; out-of-phase stereo must not cancel.
        if self.config['viz_channels'] == 'mono':
            values = np.sqrt(np.mean(bands ** 2, axis=1))
        else:
            values = np.concatenate((bands[::-1, 0], bands[:, 1]))
        dt = 1 / self.config['viz_fps'] if self.last is None else max(0, min(1, now - self.last))
        self.last = now
        peak = float(values.max())
        # A single transient must not rescale every bar in one frame.
        response = .12 if peak > self.reference else 2.5
        self.reference += (max(.01, peak) - self.reference) * (1 - math.exp(-dt / response))
        gain = (.85 / self.reference) if self.config['viz_autosens'] else self.config['viz_sensitivity'] / 100
        noise = self.config['viz_noise']
        gate = 10 ** ((-90 + noise * .35) / 20)
        floor = -60 + noise * .2
        db = 20 * np.log10(np.maximum(values * gain, 1e-12))
        levels = np.clip((db - floor) / -floor, 0, 1)
        # Fade through the noise floor instead of switching a boosted band
        # abruptly between zero and a tall bar near the gate threshold.
        levels *= np.clip((values - gate) / (gate * 3), 0, 1)
        return tuple(float(v) for v in levels)


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
        self.peak_until = []
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
        self.peak_until = []
        self.last_frame = self.last_history = 0.0
        self.history.clear()
        with self.lock:
            self.latest, self.stamp, self.message = (), 0.0, ''
        if signature is None:
            return
        tool = capture_command(config)[0]
        if not shutil.which(tool):
            self.message = f'Audio capture needs {tool}; install your audio service tools or select the other backend.'
            return
        self.stop = threading.Event()
        self.message = 'Connecting to audio output...'
        self.thread = threading.Thread(target=self._capture, args=(dict(config), self.stop), daemon=True)
        self.thread.start()

    def _capture(self, config, stop):
        process = None
        errors = b''
        try:
            analyzer = Spectrum(config)
            if stop.is_set():
                return
            process = subprocess.Popen(capture_command(config), stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            decoder = PCMBuffer()
            started = time.monotonic()
            next_analysis = started
            analyzed = -1
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 'audio')
                selector.register(process.stderr, selectors.EVENT_READ, 'error')
                while not stop.is_set():
                    for key, _ in selector.select(.02):
                        payload = os.read(key.fileobj.fileno(), 65536)
                        if not payload:
                            selector.unregister(key.fileobj)
                            if key.data == 'audio' and not stop.is_set():
                                raise RuntimeError('Audio stream closed; toggle the visualizer to retry.')
                            continue
                        if key.data == 'error':
                            errors = (errors + payload)[-2000:]
                        else:
                            decoder.feed(payload)
                    now = time.monotonic()
                    if now >= next_analysis and decoder.generation != analyzed and len(decoder.window) == FFT_SIZE * PCM_FRAME_BYTES:
                        frame = analyzer.analyze(decoder.window, now)
                        period = 1 / config['viz_fps']
                        next_analysis += max(1, math.floor((now - next_analysis) / period) + 1) * period
                        analyzed = decoder.generation
                        with self.lock:
                            if not stop.is_set() and stop is self.stop:
                                self.latest, self.stamp, self.message = frame, now, ''
                    if process.poll() is not None:
                        detail = rendering.safe_text(errors.decode(errors='replace')).strip()
                        raise RuntimeError(detail[:150] or 'Audio capture stopped; toggle the visualizer to retry.')
                    with self.lock:
                        if not stop.is_set() and stop is self.stop and not self.stamp and now - started > 5:
                            self.message = 'No audio frames. Check the output device or audio backend.'
        except ImportError:
            with self.lock:
                if not stop.is_set() and stop is self.stop:
                    self.message = 'Spectrum analysis needs NumPy; run sh install.sh to repair the installation.'
        except (OSError, ValueError, RuntimeError) as exc:
            with self.lock:
                if not stop.is_set() and stop is self.stop:
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
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
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
        if len(self.peak_until) != len(target):
            self.peak_until = [0.0] * len(target)
        attack, release = {'fast': (.035, .14), 'balanced': (.09, .28), 'soft': (.18, .45)}[config['viz_smoothing']]
        fall = {'fast': 4.0, 'balanced': 1.8, 'slow': .7}[config['viz_decay']]
        for i, value in enumerate(target):
            weight = 1 - math.exp(-dt / (attack if value > self.levels[i] else release))
            self.levels[i] += (value - self.levels[i]) * weight
            if self.levels[i] >= self.peaks[i]:
                self.peaks[i] = self.levels[i]
                self.peak_until[i] = now + .18
            elif now > self.peak_until[i]:
                self.peaks[i] = max(self.levels[i], self.peaks[i] - fall * min(dt, now - self.peak_until[i]))
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
    data = reduce_bands(levels, count)
    peak_data = reduce_bands(peaks, count)
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
    history_rows = [reduce_bands(frame, count) for frame in tuple(history)[-height:][::-1]] if layout == 'waterfall' else ()
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
                    intensity = frame[i]
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
                    if config['viz_peaks'] and amount:
                        peak = peak_data[i]
                        peak_amount = min(height, math.ceil(peak * height))
                        if peak > .015 and peak_amount > amount:
                            cell(height - peak_amount, x + dx, peak_amount / height)
    return cells


def reduce_bands(values, count):
    """Use the same band groups for bars, peaks and waterfall on resize."""
    if not values:
        return [0.0] * count
    return [max(values[i * len(values) // count:max(i * len(values) // count + 1,
                 (i + 1) * len(values) // count)]) for i in range(count)]


def fractional_caps(levels, peaks, columns, height, config):
    """Sub-cell bar edges avoid whole-row jumps on short terminal panels."""
    glyphs = {}
    if columns < 1 or height < 1 or config['viz_layout'] != 'bars' or config['viz_glyph'] != 'blocks' or not levels:
        return glyphs
    count = min(len(levels), columns)
    data, peak_data = reduce_bands(levels, count), reduce_bands(peaks, count)
    for i in range(count):
        value = data[i]
        start = i * columns // count
        span = max(1, (i + 1) * columns // count - start - config['viz_gap'])
        units = value * height
        whole = math.floor(units)
        fraction = units - whole
        if value > .015 and fraction > 0:
            row = height - whole - 1
            char = '▁▂▃▄▅▆▇█'[min(7, max(0, math.ceil(fraction * 8) - 1))]
            for dx in range(span):
                glyphs[row, start + dx] = char
        if config['viz_peaks'] and value > .015:
            peak = peak_data[i]
            if peak > .015 and math.ceil(peak * height) > math.ceil(value * height):
                row = max(0, height - math.ceil(peak * height))
                for dx in range(span):
                    glyphs[row, start + dx] = '▔'
    return glyphs


def draw(screen, engine, config, panel):
    if panel is None:
        return
    y, height = panel
    rows_available, width = screen.getmaxyx()
    height = min(height, rows_available - 1 - y)
    if width < 5 or height < 1 or y < 0:
        return
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
    caps = fractional_caps(levels, engine.peaks, columns, height, config)
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
        char = (' .:-=+*#%@'[min(9, int(intensity * 9))] if waterfall else caps.get((row, col), glyph))
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
