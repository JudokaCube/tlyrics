import copy
import curses
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import rendering
import settings
import visualizer


class VisualizerTests(unittest.TestCase):
    def test_partial_frames_keep_newest_complete_frame(self):
        decoder = visualizer.FrameDecoder(4)
        self.assertIsNone(decoder.feed(bytes([0, 1])))
        self.assertEqual(decoder.feed(bytes([2, 3, 4, 5, 6, 7, 8])), tuple(v / 255 for v in (4, 5, 6, 7)))
        self.assertEqual(decoder.pending, bytes([8]))
        self.assertEqual(decoder.feed(bytes([9, 10, 11])), tuple(v / 255 for v in (8, 9, 10, 11)))

    def test_default_off_never_starts_capture(self):
        engine = visualizer.Visualizer()
        with patch.object(visualizer.subprocess, 'Popen') as process:
            engine.configure(settings.DEFAULTS)
            engine.close()
            process.assert_not_called()

    def test_missing_cava_is_nonfatal(self):
        engine = visualizer.Visualizer()
        with patch.object(visualizer.shutil, 'which', return_value=None):
            engine.configure(dict(settings.DEFAULTS, visualizer=True))
        self.assertIn('Install cava', engine.message)
        self.assertIsNone(engine.thread)
        engine.close()

    def test_capture_config_uses_output_monitor_and_rejects_injection(self):
        config = dict(settings.DEFAULTS)
        self.assertIn('source = auto', visualizer.capture_config(config))
        self.assertIn('method = raw', visualizer.capture_config(config))
        for bad in ['alsa_input.microphone', 'auto\n[output]', 'x.monitor\nfoo=bar']:
            with self.assertRaises(ValueError):
                visualizer.capture_config(dict(config, viz_source=bad))
        self.assertIn('source = auto', visualizer.capture_config(dict(config, viz_backend='pipewire', viz_source='x.monitor')))

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
            script = Path(directory) / 'cava'
            pidfile = Path(directory) / 'pid'
            script.write_text('#!/usr/bin/env python3\nimport os,time\n'
                              f'open({str(pidfile)!r},"w").write(str(os.getpid()))\n'
                              'while True:\n os.write(1,bytes(range(32))[:13])\n'
                              ' os.write(1,bytes(range(32))[13:])\n time.sleep(.01)\n')
            script.chmod(0o755)
            engine = visualizer.Visualizer()
            with patch.dict(os.environ, {'PATH': directory + os.pathsep + os.environ['PATH']}):
                engine.configure(dict(settings.DEFAULTS, visualizer=True))
                try:
                    deadline = time.monotonic() + 3
                    while not engine.latest and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(len(engine.latest), 32, engine.message)
                    self.assertAlmostEqual(engine.latest[-1], 31 / 255)
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
            settings.save(config)
            self.assertEqual(settings.load()['viz_source'], 'auto')

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
        with patch.object(visualizer.shutil, 'which', return_value='/usr/bin/cava'), \
             patch.object(visualizer.subprocess, 'Popen', side_effect=OSError('backend unavailable')):
            engine.configure(dict(settings.DEFAULTS, visualizer=True))
            engine.thread.join(timeout=2)
        self.assertIn('backend unavailable', engine.message)
        engine.configure(settings.DEFAULTS)
        self.assertIsNone(engine.thread)
        self.assertEqual(engine.message, '')
