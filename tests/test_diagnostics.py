import contextlib
import concurrent.futures
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import diagnostics
import lyrics
import settings
import tlyrics
import visualizer


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('ROOT', self.root), ('LOG', self.root / 'diagnostics.log')]:
            patcher = patch.object(diagnostics, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = dict(settings.DEFAULTS)
        diagnostics.configure(self.config)
        self.addCleanup(lambda: diagnostics.configure(dict(self.config, log_level='off')))

    def test_errors_have_code_locations_without_private_exception_payload(self):
        try:
            raise ValueError('secret-token /home/private/song.lrc')
        except ValueError as exc:
            diagnostics.record('Lyrics loading failed', exc)
        text = diagnostics.read()
        self.assertIn('ValueError', text)
        self.assertIn('test_diagnostics.py:', text)
        self.assertNotIn('secret-token', text)
        self.assertNotIn('/home/private', text)
        self.assertEqual(diagnostics.LOG.stat().st_mode & 0o777, 0o600)

    def test_levels_deduplication_clear_and_disable(self):
        diagnostics.record('Hidden debug', debug=True)
        self.assertNotIn('Hidden debug', diagnostics.read())
        diagnostics.record('Repeated failure')
        diagnostics.record('Repeated failure')
        self.assertEqual(diagnostics.read().count('Repeated failure'), 1)
        diagnostics.configure(dict(self.config, log_level='off'))
        diagnostics.record('Disabled failure')
        self.assertNotIn('Disabled failure', diagnostics.read())
        diagnostics.configure(dict(self.config, log_level='debug'))
        diagnostics.record('Debug event', debug=True)
        self.assertIn('Debug event', diagnostics.read())
        diagnostics.clear()
        self.assertEqual(diagnostics.read(), 'No diagnostics recorded.')

    def test_rotation_retention_and_file_permissions(self):
        for handler in diagnostics.logger.handlers:
            if isinstance(handler, diagnostics.PrivateLog):
                handler.maxBytes = 100
        for i in range(20):
            diagnostics.record(f'Failure number {i}')
        paths = list(self.root.glob('diagnostics.log*'))
        self.assertLessEqual(len(paths), 3)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in paths))
        diagnostics.configure(dict(self.config, log_files=2))
        self.assertFalse((self.root / 'diagnostics.log.2').exists())

    def test_export_omits_private_settings_and_refuses_overwrite(self):
        cfg = dict(self.config, player='private-player', viz_source='private.monitor', aliases=['private'], song_offsets={'private': 1})
        path = diagnostics.export(self.root / 'report.json', cfg)
        report = json.loads(path.read_text())
        self.assertNotIn('private', path.read_text())
        self.assertIn('python', report)
        self.assertIn('show_diagnostics', report['settings'])
        with self.assertRaises(FileExistsError):
            diagnostics.export(path, cfg)

    def test_background_errors_are_captured_without_terminal_output(self):
        stream = io.StringIO()
        hook = threading.excepthook
        def fail():
            raise RuntimeError('private thread message')
        with contextlib.redirect_stderr(stream), diagnostics.quiet_errors():
            thread = threading.Thread(target=fail)
            thread.start()
            thread.join()
        self.assertEqual(stream.getvalue(), '')
        self.assertIs(threading.excepthook, hook)
        self.assertIn('Background thread failed [RuntimeError]', diagnostics.read())
        self.assertNotIn('private thread message', diagnostics.read())

    def test_visualizer_error_is_only_drawn_when_requested(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (30, 100)
        engine = visualizer.Visualizer()
        engine.message = 'Backend error'
        visualizer.draw(screen, engine, self.config, (20, 4))
        screen.addstr.assert_not_called()
        visualizer.draw(screen, engine, dict(self.config, show_diagnostics=True), (20, 4))
        self.assertIn('Backend error', str(screen.addstr.call_args))

    def test_playback_technical_labels_are_opt_in(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (30, 100)
        screen.getch.return_value = ord('q')
        players = MagicMock()
        players.bridge = None
        players.changed.is_set.return_value = False
        players.read.return_value = [tlyrics.Track('limusic', 'Song', 'Artist', '', 180, 2, 'Paused', service='limusic')]
        updater = MagicMock()
        updater.poll.return_value = None
        updater.pending = None
        updater.message = 'Private update error'
        pool = MagicMock()
        def submit(fn, *args):
            result = concurrent.futures.Future()
            result.set_result(fn(*args))
            return result
        pool.submit.side_effect = submit
        doc = lyrics.parse_lrc('[00:01]<00:01>one <00:02>two<00:03>')
        with patch.object(tlyrics, 'setup_input'), patch('curses.raw'), patch('curses.curs_set'), \
             patch('curses.has_colors', return_value=False), patch.object(tlyrics, 'apply_theme', return_value=0), \
             patch.object(tlyrics.concurrent.futures, 'ThreadPoolExecutor', return_value=pool), \
             patch.object(tlyrics.updates, 'Manager', return_value=updater), \
             patch.object(tlyrics, 'load_lyrics', return_value=({}, doc)):
            for enabled in (False, True):
                screen.addnstr.reset_mock()
                tlyrics.ui(screen, players, SimpleNamespace(lrc=None, settings=False), dict(self.config, show_diagnostics=enabled))
                text = ' '.join(call.args[2] for call in screen.addnstr.call_args_list)
                self.assertEqual('Word sync' in text, enabled)
                self.assertEqual('offset' in text, enabled)
                self.assertEqual('Private update error' in text, enabled)

    def test_cli_crash_logs_without_printing_exception(self):
        stream = io.StringIO()
        with patch('sys.argv', ['tlyrics', '--status']), patch.object(settings, 'load', return_value=self.config), \
             patch.object(tlyrics, 'Players', side_effect=RuntimeError('private crash')), contextlib.redirect_stderr(stream):
            self.assertEqual(tlyrics.main(), 1)
        self.assertEqual(stream.getvalue(), '')
        self.assertIn('App command failed [RuntimeError]', diagnostics.read())
