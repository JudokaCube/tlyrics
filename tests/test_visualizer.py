import copy
import curses
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import rendering
import settings
import visualizer


class VisualizerTests(unittest.TestCase):
    def test_cancelled_worker_does_not_use_replacement_stop_event(self):
        engine = visualizer.Visualizer()
        original_stop = threading.Event()
        def slow_setup(config):
            original_stop.set()
            engine.stop = threading.Event()
        with patch.object(visualizer, 'Spectrum', side_effect=slow_setup), \
             patch.object(visualizer.subprocess, 'Popen') as process:
            engine._capture(settings.DEFAULTS, original_stop)
        process.assert_not_called()

    def test_partial_pcm_keeps_alignment_and_bounded_latest_window(self):
        decoder = visualizer.PCMBuffer()
        payload = bytes(range(256)) * 33
        self.assertIsNone(decoder.feed(payload[:3]))
        self.assertEqual(decoder.feed(payload[3:8192]), payload[:8192])
        decoder.feed(payload[8192:8195])
        self.assertEqual(len(decoder.pending), 3)
        self.assertEqual(decoder.feed(payload[8195:8196]), payload[4:8196])
        self.assertEqual(len(decoder.window), visualizer.FFT_SIZE * 4)
        self.assertEqual(decoder.pending, b'')

    def test_default_off_never_starts_capture(self):
        engine = visualizer.Visualizer()
        with patch.object(visualizer.subprocess, 'Popen') as process:
            engine.configure(settings.DEFAULTS)
            engine.close()
            process.assert_not_called()

    def test_missing_capture_tool_is_nonfatal(self):
        engine = visualizer.Visualizer()
        with patch.object(visualizer.shutil, 'which', return_value=None):
            engine.configure(dict(settings.DEFAULTS, visualizer=True))
        self.assertIn('needs parec', engine.message)
        self.assertIsNone(engine.thread)
        engine.close()

    def test_capture_command_uses_output_monitor_and_rejects_injection(self):
        config = dict(settings.DEFAULTS)
        self.assertIn('--device=@DEFAULT_MONITOR@', visualizer.capture_command(config))
        self.assertEqual(visualizer.capture_command(config)[0], 'parec')
        for bad in ['alsa_input.microphone', 'auto\n[output]', 'x.monitor\nfoo=bar']:
            with self.assertRaises(ValueError):
                visualizer.capture_command(dict(config, viz_source=bad))
        command = visualizer.capture_command(dict(config, viz_backend='pipewire', viz_source='x.monitor'))
        self.assertEqual(command[0], 'pw-record')
        self.assertIn('stream.capture.sink=true', ' '.join(command))

    def test_input_discovery_only_offers_monitors(self):
        result = MagicMock(stdout='1 alsa_input.mic x\n2 alsa_output.speaker.monitor x\n')
        with patch.object(visualizer.shutil, 'which', return_value='/usr/bin/pactl'), \
             patch.object(visualizer.subprocess, 'run', return_value=result):
            self.assertEqual(visualizer.sources(), ['auto', 'alsa_output.speaker.monitor'])

    def test_all_layouts_stay_inside_panel_and_silence_is_empty(self):
        for layout in settings.CHOICES['viz_layout']:
            config = dict(settings.DEFAULTS, viz_layout=layout)
            for w, h in [(1, 1), (7, 3), (80, 5), (140, 10)]:
                cells = visualizer.grid([.2, .6, 1, .4], [1] * 4, [[.5] * 4] * 20, w, h, config)
                self.assertTrue(cells, layout)
                self.assertTrue(all(0 <= y < h and 0 <= x < w and 0 < v <= 1 for (y, x), v in cells.items()))
                self.assertEqual(visualizer.grid([0] * 4, [0] * 4, [[0] * 4], w, h, config), {})

    def test_regions_do_not_overlap_lyrics(self):
        for h in range(5, 60):
            for position in ['top', 'bottom']:
                config = dict(settings.DEFAULTS, visualizer=True, viz_position=position, viz_height=10)
                top, bottom, panel = visualizer.region(h, config)
                if panel:
                    y, size = panel
                    self.assertGreater(bottom, top)
                    self.assertTrue(y + size <= top or bottom <= y)
                    self.assertLessEqual(y + size, h - 3)
                else:
                    self.assertLess(h, 18)

    def test_smoothing_is_time_based_and_stale_audio_decays(self):
        engine = visualizer.Visualizer()
        engine.latest, engine.stamp = (1.0,) * 4, 100
        engine.last_frame = 99.99
        first = engine.frame(settings.DEFAULTS, 100)[0]
        self.assertTrue(0 < first < 1)
        for i in range(1, 150):
            frame = engine.frame(settings.DEFAULTS, 100 + i / 30)
        self.assertLess(max(frame), .001)
        self.assertLessEqual(len(engine.history), 20)

    def test_worker_handles_chunked_pipe_and_stops_process(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'parec'
            pidfile = Path(directory) / 'pid'
            script.write_text('#!/usr/bin/env python3\nimport os,time\n'
                              f'open({str(pidfile)!r},"w").write(str(os.getpid()))\n'
                              'while True:\n os.write(1,bytes(8192)[:13])\n'
                              ' os.write(1,bytes(8192)[13:])\n time.sleep(.01)\n')
            script.chmod(0o755)
            engine = visualizer.Visualizer()
            with patch.dict(os.environ, {'PATH': directory + os.pathsep + os.environ['PATH']}):
                engine.configure(dict(settings.DEFAULTS, visualizer=True))
                try:
                    deadline = time.monotonic() + 3
                    while not engine.latest and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(len(engine.latest), 32, engine.message)
                    self.assertEqual(engine.latest, (0.0,) * 32)
                    thread = engine.thread
                finally:
                    engine.close()
            self.assertFalse(thread.is_alive())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)

    def test_draw_supports_monochrome_and_small_terminal(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (18, 20)
        engine = visualizer.Visualizer()
        engine.latest, engine.stamp = (.5,) * 32, time.monotonic()
        with patch('curses.has_colors', return_value=False):
            for layout in settings.CHOICES['viz_layout']:
                visualizer.draw(screen, engine, dict(settings.DEFAULTS, viz_layout=layout), (12, 3))
        self.assertTrue(screen.addstr.called)


class AppearanceTests(unittest.TestCase):
    def test_presets_only_contain_valid_settings(self):
        for preset in settings.PRESETS.values():
            for key, value in preset.items():
                self.assertIn(value, settings.CHOICES[key])
            self.assertNotIn('visualizer', preset)
            self.assertNotIn('player', preset)

    def test_settings_roundtrip_and_invalid_source(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(settings, 'CONFIG', Path(directory) / 'settings.json'):
            config = copy.deepcopy(settings.DEFAULTS)
            config.update(visualizer=True, viz_source='alsa_output.test.monitor', border='rounded', viz_layout='radial')
            settings.save(config)
            self.assertEqual(settings.load(), config)
            config['viz_source'] = 'microphone\nsource = other'
            with self.assertRaises(ValueError):
                settings.save(config)
            self.assertEqual(settings.load()['viz_source'], 'alsa_output.test.monitor')

    def test_progress_and_chrome(self):
        for style in settings.CHOICES['progress_style']:
            self.assertEqual(len(rendering.progress_bar(.5, 20, style)), 20)
        screen = MagicMock()
        screen.getmaxyx.return_value = (20, 60)
        with patch('curses.has_colors', return_value=False):
            for border in settings.CHOICES['border']:
                rendering.chrome(screen, dict(settings.DEFAULTS, border=border))
        self.assertTrue(screen.addstr.called)

    def test_basic_colour_terminal_uses_supported_colours(self):
        with patch('curses.has_colors', return_value=True), patch('curses.start_color'), \
             patch('curses.use_default_colors'), patch('curses.COLORS', 8, create=True), \
             patch('curses.COLOR_PAIRS', 64, create=True), patch('curses.color_pair', return_value=0), \
             patch('curses.init_pair') as init:
            for preset in settings.PRESETS.values():
                rendering.configure(dict(settings.DEFAULTS, **preset))
            self.assertTrue(all(-1 <= call.args[1] < 8 and -1 <= call.args[2] < 8 for call in init.call_args_list))

    def test_failed_process_start_reports_error_and_can_be_disabled(self):
        engine = visualizer.Visualizer()
        with patch.object(visualizer.shutil, 'which', return_value='/usr/bin/parec'), \
             patch.object(visualizer.subprocess, 'Popen', side_effect=OSError('backend unavailable')):
            engine.configure(dict(settings.DEFAULTS, visualizer=True))
            engine.thread.join(timeout=2)
        self.assertIn('backend unavailable', engine.message)
        engine.configure(settings.DEFAULTS)
        self.assertIsNone(engine.thread)
        self.assertEqual(engine.message, '')


class NativeSpectrumTests(unittest.TestCase):
    @staticmethod
    def tone(frequency, amplitude=.5, stereo=False, opposite=False):
        import numpy as np
        wave = np.sin(np.arange(visualizer.FFT_SIZE) * 2 * np.pi * frequency / visualizer.SAMPLE_RATE) * amplitude
        samples = np.column_stack((wave, -wave if opposite else np.zeros_like(wave) if stereo else wave))
        return (samples * 32767).astype('=i2').tobytes()

    def test_tone_peaks_in_correct_logarithmic_band(self):
        import numpy as np
        for frequency in (100, 440, 1000, 6000, 12000):
            analyzer = visualizer.Spectrum(dict(settings.DEFAULTS, viz_channels='mono', viz_autosens=False))
            levels = analyzer.analyze(self.tone(frequency), 100)
            peak = int(np.argmax(levels))
            self.assertLessEqual(analyzer.left[peak] * visualizer.SAMPLE_RATE / visualizer.FFT_SIZE, frequency)
            self.assertGreaterEqual(analyzer.right[peak] * visualizer.SAMPLE_RATE / visualizer.FFT_SIZE, frequency)
            self.assertTrue(all(math.isfinite(v) and 0 <= v <= 1 for v in levels))

    def test_stereo_channels_are_separate_and_mono_does_not_cancel(self):
        analyzer = visualizer.Spectrum(settings.DEFAULTS)
        levels = analyzer.analyze(self.tone(1000, stereo=True), 100)
        self.assertGreater(max(levels[:16]), .5)
        self.assertEqual(max(levels[16:]), 0)
        mono = visualizer.Spectrum(dict(settings.DEFAULTS, viz_channels='mono'))
        self.assertGreater(max(mono.analyze(self.tone(1000, opposite=True), 100)), .5)

    def test_silence_noise_gate_and_sensitivity(self):
        cfg = dict(settings.DEFAULTS, viz_autosens=False)
        analyzer = visualizer.Spectrum(cfg)
        self.assertEqual(analyzer.analyze(bytes(visualizer.FFT_SIZE * 4), 100), (0.0,) * 32)
        self.assertEqual(max(analyzer.analyze(self.tone(1000, amplitude=.0001), 101)), 0)
        low = visualizer.Spectrum(dict(cfg, viz_sensitivity=50)).analyze(self.tone(1000, .1), 100)
        high = visualizer.Spectrum(dict(cfg, viz_sensitivity=200)).analyze(self.tone(1000, .1), 100)
        self.assertGreater(max(high), max(low))

    def test_bar_tops_use_fractional_cells(self):
        config = dict(settings.DEFAULTS, viz_layout='bars', viz_peaks=False)
        caps = visualizer.fractional_caps([.45], [.45], 5, 4, config)
        self.assertTrue(caps)
        self.assertIn('▇', caps.values())
        self.assertTrue(all(0 <= y < 4 and 0 <= x < 5 for y, x in caps))
