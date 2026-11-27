import concurrent.futures
import copy
import curses
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import experience
import lyrics
import settings
import tlyrics
import visualizer


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.doc = lyrics.parse_lrc('[00:10]one\n[00:20]two\n[00:30]three\n[00:40]four')

    def test_preparation_leaves_reading_time_and_handles_song_end(self):
        calibration = experience.Calibration(self.doc, 0, 0)
        calibration.ready = False
        self.assertTrue(calibration.prepare(8, 0))
        self.assertEqual(calibration.lines[calibration.index].start, 20)
        calibration.ready = False
        self.assertFalse(calibration.prepare(39, 0))
        self.assertFalse(calibration.ready)

    def test_taps_produce_correct_per_song_offset_with_global_adjustment(self):
        calibration = experience.Calibration(self.doc, 5, 0)
        for position in (10.8, 20.9, 30.85):
            calibration.tap(position)
        self.assertAlmostEqual(calibration.adjustment(.2), -1.05)
        # A lyric timestamp plus the resulting offset aligns with the tapped audio time.
        self.assertAlmostEqual(30.85 + .2 + calibration.adjustment(.2), 30)

    def test_repeated_line_taps_do_not_count_and_inconsistent_taps_rejected(self):
        calibration = experience.Calibration(self.doc, 5, 0)
        calibration.tap(10)
        calibration.move(-1)
        calibration.tap(10.1)
        self.assertEqual(len(calibration.samples), 1)
        with self.assertRaises(ValueError):
            calibration.adjustment(0)
        calibration.tap(22)
        calibration.tap(34)
        with self.assertRaises(ValueError):
            calibration.adjustment(0)

    def test_outlier_rejected_without_skewing_consistent_samples(self):
        calibration = experience.Calibration(self.doc, 5, 0)
        for position in (9.5, 19.5, 29.5, 35):
            calibration.tap(position)
        self.assertEqual(calibration.adjustment(0), .5)

    def test_bounds_missing_time_and_first_word_anchor(self):
        doc = lyrics.parse_lrc('[00:01]<00:02>hello <00:03>world\n[00:04]next')
        calibration = experience.Calibration(doc, 0, 0)
        self.assertEqual(calibration.lines[0].start, 2)
        for position in (None, float('nan'), 1000):
            with self.assertRaises(ValueError):
                calibration.tap(position)
        with self.assertRaises(ValueError):
            experience.Calibration(lyrics.Document(), 0, 0)


class SourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = patch.object(settings, 'CONFIG', self.root / 'settings.json')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.track = tlyrics.Track('limusic', 'Test', 'Artist', '', 180, 0, 'Playing')

    def test_manual_choice_persists_independently_of_cache_and_other_songs(self):
        record = {'syncedLyrics': '[00:01]<00:01>hello <00:02>world', '_tlyrics_source': 'Local file'}
        experience.select(self.track, record)
        with patch.object(tlyrics, 'get_lyrics') as fetch:
            saved, doc = tlyrics.load_lyrics(self.track, refresh=True, cache_enabled=False)
            self.assertEqual(saved, record)
            self.assertTrue(doc.has_words)
            fetch.assert_not_called()
        other = tlyrics.Track('limusic', 'Other', 'Artist', '', 180, 0, 'Playing')
        self.assertIsNone(experience.selected(other))
        experience.automatic(self.track)
        self.assertIsNone(experience.selected(self.track))

    def test_local_file_validation_preserves_previous_choice(self):
        original = {'syncedLyrics': '[00:01]valid'}
        experience.select(self.track, original)
        path = self.root / 'song.LRC'
        path.write_text('[00:01]new')
        self.assertIn('syncedLyrics', experience.local_record(path))
        with self.assertRaises(ValueError):
            experience.select(self.track, {'syncedLyrics': 'not a timed file'})
        self.assertEqual(experience.selected(self.track), original)
        path.write_bytes(b'x' * (lyrics.MAX_BYTES + 1))
        with self.assertRaises(ValueError):
            experience.local_record(path)

    def test_search_validates_response_and_reuses_throttle(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps([
            {'id': 1, 'syncedLyrics': '[00:01]hello'}]).encode()
        with patch('urllib.request.urlopen', return_value=response) as request, \
             patch.multiple(tlyrics, NEXT_REQUEST=0.0, RETRY_UNTIL=0.0):
            result = tlyrics.fetch_record({'track_name': 'a & b'}, 'search')
            self.assertEqual(result[0]['id'], 1)
            self.assertIn('/api/search?track_name=a+%26+b', request.call_args.args[0].full_url)
            response.__enter__.return_value.read.return_value = b'[null]'
            self.assertIn('message', tlyrics.fetch_record({'track_name': 'a'}, 'search'))

    def test_cancelled_search_does_not_write_selection(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.return_value = 27
        future = concurrent.futures.Future()
        self.assertIsNone(tlyrics.wait_source(screen, future))
        self.assertTrue(future.cancelled())
        self.assertIsNone(experience.selected(self.track))

    def test_source_menu_local_selection_and_automatic_reset(self):
        screen, pool = MagicMock(), MagicMock()
        path = self.root / 'song.lrc'
        path.write_text('[00:01]hello')
        with patch.object(tlyrics, 'choose', return_value='Load local lyrics file'), \
             patch.object(tlyrics, 'prompt', return_value=str(path)):
            data, doc = tlyrics.source_menu(screen, self.track, {}, lyrics.Document(), pool)
        self.assertEqual(data['_tlyrics_source'], 'Local file')
        result = concurrent.futures.Future()
        result.set_result({'syncedLyrics': '[00:02]automatic'})
        pool.submit.return_value = result
        with patch.object(tlyrics, 'choose', return_value='Use automatic lookup'):
            data, doc = tlyrics.source_menu(screen, self.track, data, doc, pool, False)
        self.assertIsNone(experience.selected(self.track))
        self.assertEqual(doc.lines[0].start, 2)
        self.assertEqual(pool.submit.call_args.args[-1], False)


class PreviewTests(unittest.TestCase):
    def test_preview_changes_only_draft_and_never_captures_audio(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (24, 80)
        screen.getch.side_effect = [curses.KEY_RIGHT, curses.KEY_RESIZE, 27]
        config = settings.Preferences(copy.deepcopy(settings.DEFAULTS))
        draft = settings.clone(config)
        with patch('curses.has_colors', return_value=False), patch.object(tlyrics, 'apply_theme'), \
             patch.object(visualizer.Visualizer, 'configure') as capture, patch.object(settings, 'save') as save:
            tlyrics.appearance_preview(screen, draft)
        self.assertNotEqual(draft['theme'], config['theme'])
        self.assertEqual(config['theme'], settings.DEFAULTS['theme'])
        capture.assert_not_called()
        save.assert_not_called()
        screen.clearok.assert_called_once_with(True)
        screen.timeout.assert_called_with(-1)

    def test_presets_are_valid_and_keep_audio_device_and_other_preferences(self):
        for preset in settings.VISUALIZER_PRESETS.values():
            for key, value in preset.items():
                self.assertIn(value, settings.CHOICES[key])
                self.assertTrue(key.startswith('viz_'))
            self.assertNotIn('viz_backend', preset)
            self.assertNotIn('viz_source', preset)
            self.assertFalse(preset['viz_peaks'])


class PlaybackFlowsTests(unittest.TestCase):
    def run_flow(self, screen, config, record, source_result=None, pending=None):
        players = MagicMock()
        track = tlyrics.Track('limusic', 'Calibration test', 'Artist', '', 180, 0, 'Playing', service='limusic')
        players.bridge = None
        players.changed.is_set.return_value = False
        players.read.return_value = [track]
        updater = MagicMock()
        updater.poll.return_value = None
        updater.pending = None
        updater.message = ''
        pool = MagicMock()
        def submit(fn, *args):
            if pending is not None and fn is tlyrics.load_lyrics:
                return pending
            future = concurrent.futures.Future()
            future.set_result(fn(*args))
            return future
        pool.submit.side_effect = submit
        clock = MagicMock()
        clock.seconds.side_effect = lambda: screen.position
        clock.cadence = 0
        with patch.object(tlyrics, 'setup_input'), patch('curses.raw'), patch('curses.curs_set'), \
             patch('curses.has_colors', return_value=False), patch.object(tlyrics, 'apply_theme', return_value=0), \
             patch.object(tlyrics.concurrent.futures, 'ThreadPoolExecutor', return_value=pool), \
             patch.object(tlyrics.updates, 'Manager', return_value=updater), \
             patch.object(tlyrics, 'PlaybackClock', return_value=clock), \
             patch.object(tlyrics, 'choose', return_value='Save'), \
             patch.object(tlyrics, 'source_menu', side_effect=source_result), \
             patch.object(tlyrics, 'load_lyrics', return_value=({}, record)):
            tlyrics.ui(screen, players, SimpleNamespace(lrc=None, settings=False), config)
        return track

    def test_calibration_keyboard_flow_saves_only_song_offset(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (14, 40)
        screen.position = 0
        keys = iter([(ord('c'), 0), (10, 0), (ord(' '), 10.8), (ord(' '), 20.9),
                     (ord(' '), 30.85), (10, 31), (ord('q'), 31)])
        def key():
            result, screen.position = next(keys)
            return result
        screen.getch.side_effect = key
        doc = lyrics.parse_lrc('[00:10]one\n[00:20]two\n[00:30]three\n[00:40]four')
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, 'CONFIG', Path(folder) / 'settings.json'):
            config = settings.load()
            config.update(offset=.2, show_hints=False)
            track = self.run_flow(screen, config, doc)
            self.assertEqual(settings.load()['song_offsets'][settings.song_key(track)], -1.05)
            self.assertEqual(settings.load()['offset'], .2)
        text = ' '.join(call.args[2] for call in screen.addnstr.call_args_list)
        self.assertIn('Space tap', text)  # Calibration controls work with ordinary hints hidden.

    def test_manual_source_replaces_pending_auto_result(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (24, 80)
        screen.position = 2
        screen.getch.side_effect = [ord('l'), ord('q')]
        pending = concurrent.futures.Future()
        old = lyrics.parse_lrc('[00:01]old automatic lyrics')
        replacement = lyrics.parse_lrc('[00:01]chosen replacement')
        def select(*args):
            pending.set_result(({}, old))
            return {}, replacement
        self.run_flow(screen, copy.deepcopy(settings.DEFAULTS), old, select, pending)
        text = ' '.join(call.args[2] for call in screen.addstr.call_args_list)
        self.assertIn('chosen replacement', text)
        self.assertNotIn('old automatic lyrics', text)

    def test_calibration_cancel_does_not_save_or_quit(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (24, 80)
        screen.position = 2
        screen.getch.side_effect = [ord('c'), ord(' '), 27, ord('q')]
        config = copy.deepcopy(settings.DEFAULTS)
        doc = lyrics.parse_lrc('[00:10]one\n[00:20]two\n[00:30]three')
        with patch.object(settings, 'save') as save:
            self.run_flow(screen, config, doc)
        self.assertEqual(config['song_offsets'], {})
        save.assert_not_called()
        self.assertEqual(screen.getch.call_count, 4)
