import math
import unittest
from unittest.mock import patch
import yaml
import lyrics
import rendering
import settings
from timing import PlaybackClock
from tlyrics import Track


def observation(position, sampled, **kwargs):
    return Track('test', 'Test', 'Artist', '', 300, position, kwargs.pop('status', 'Playing'),
                 sampled=sampled, **kwargs)


class ClockTests(unittest.TestCase):
    def test_precise_samples_suppress_jitter(self):
        clock = PlaybackClock()
        errors = []
        for i in range(200):
            now = 100 + i / 10
            jitter = 0.018 if i % 2 else -0.018
            clock.update(observation(20 + i / 10 + jitter, now))
            errors.append(abs(clock.seconds(now + .04) - (20 + i / 10 + .04)))
        self.assertLess(max(errors), .025)
        self.assertLess(sum(errors[10:]) / len(errors[10:]), .01)

    def test_coarse_samples_do_not_reset_every_frame(self):
        clock = PlaybackClock()
        errors = []
        for i in range(100):
            elapsed = i * .1 + .025
            clock.update(observation(math.floor(elapsed), 100 + elapsed, resolution=1))
            if i > 15:
                errors.append(abs(clock.seconds(100 + elapsed) - elapsed))
        self.assertLess(max(errors), .08)

    def test_precise_seek_pause_rate_and_track_change(self):
        clock = PlaybackClock()
        clock.update(observation(20, 100))
        clock.update(observation(100, 100.1))
        self.assertAlmostEqual(clock.seconds(100.1), 100)
        clock.update(observation(10, 100.2))
        self.assertAlmostEqual(clock.seconds(100.2), 10)
        clock.update(observation(10.1, 100.3, status='Paused'))
        self.assertAlmostEqual(clock.seconds(110), 10.1)
        clock.update(observation(10.1, 110, rate=2))
        self.assertAlmostEqual(clock.seconds(110.1), 10.3)
        clock.update(observation(0, 110.2, track_id='new'))
        self.assertAlmostEqual(clock.seconds(110.2), 0)

    def test_seek_event_handles_small_seeks(self):
        clock = PlaybackClock()
        clock.update(observation(20, 100))
        clock.update(observation(20.3, 100.1, seek_serial=1))
        self.assertAlmostEqual(clock.seconds(100.1), 20.3)

    def test_stale_sample_freezes_and_missing_position_clears(self):
        clock = PlaybackClock()
        clock.update(observation(20, 100))
        self.assertLessEqual(clock.seconds(110), 21.5)
        clock.update(observation(None, 111))
        self.assertIsNone(clock.seconds(111))

    def test_identical_bridge_observation_is_not_reapplied(self):
        clock = PlaybackClock()
        t = observation(20, 100)
        clock.update(t)
        first = clock.seconds(100.1)
        clock.update(t)
        self.assertEqual(clock.seconds(100.1), first)


class WordTests(unittest.TestCase):
    def test_enhanced_lrc_offset_repeated_lines_and_end(self):
        doc = lyrics.parse_lrc('[offset:100]\n[00:01.00][00:04.00]<00:01.00>Hello <00:01.50>world<00:02.00>')
        self.assertEqual([line.text for line in doc.lines], ['Hello world', 'Hello world'])
        self.assertAlmostEqual(doc.lines[0].start, .9)
        self.assertAlmostEqual(doc.lines[1].words[1].start, 4.4)
        self.assertAlmostEqual(doc.lines[0].end, 1.9)
        self.assertFalse(doc.active(0, 2.5))
        self.assertEqual([state for _, state, _ in lyrics.word_states(doc.lines[0], 1.6, 1.9)], ['past', 'active'])

    def test_line_only_never_invents_word_timing(self):
        doc = lyrics.parse_lrc('[00:01]Hello world\n[00:03]Another line')
        self.assertFalse(doc.has_words)
        self.assertEqual(doc.index(.5), -1)
        self.assertEqual(doc.index(3), 1)

    def record(self):
        return {'version': '1.0', 'metadata': {}, 'lines': [
            {'text': 'one two', 'start_ms': 1000, 'end_ms': 4000, 'words': [
                {'text': 'one ', 'start_ms': 1000, 'end_ms': 1500},
                {'text': 'two', 'start_ms': 2000, 'end_ms': 3500}]},
            {'text': 'overlap', 'start_ms': 3000, 'end_ms': 5000}]}

    def test_yaml_gaps_overlaps_and_word_boundaries(self):
        doc = lyrics.parse_lyricsfile(yaml.safe_dump(self.record()))
        self.assertTrue(doc.has_words)
        states = lyrics.word_states(doc.lines[0], 1.75, doc.line_end(0))
        self.assertEqual([s for _, s, _ in states], ['past', 'future'])
        self.assertTrue(doc.active(0, 3.1))
        self.assertTrue(doc.active(1, 3.1))
        self.assertFalse(doc.active(0, 4))

    def test_bad_yaml_rejected_and_lrc_fallback(self):
        for bad in ('version: "1.0"\nversion: "2.0"', 'x: &a [1]\ny: *a',
                    '!!python/object/apply:os.system ["echo bad"]', 'version: "2.0"'):
            with self.assertRaises(ValueError):
                lyrics.parse_lyricsfile(bad)
            doc = lyrics.from_record({'lyricsfile': bad, 'syncedLyrics': '[00:01]fallback'})
            self.assertEqual(doc.lines[0].text, 'fallback')
            self.assertTrue(doc.warning)

    def test_invalid_word_text_falls_back_to_line(self):
        record = self.record()
        record['lines'][0]['words'][0]['text'] = 'wrong '
        doc = lyrics.parse_lyricsfile(yaml.safe_dump(record))
        self.assertFalse(doc.has_words)
        self.assertEqual(doc.lines[0].text, 'one two')

    def test_invalid_timestamp_and_deep_yaml(self):
        with self.assertRaises(ValueError):
            lyrics.parse_lrc('[00:99]bad')
        with self.assertRaises(ValueError):
            lyrics.yaml_document('[' * 20 + '0' + ']' * 20)
        record = self.record()
        record['lines'][0]['words'][0]['start_ms'] = -1
        with self.assertRaises(ValueError):
            lyrics.parse_lyricsfile(yaml.safe_dump(record))


class RenderTests(unittest.TestCase):
    def test_wide_characters_and_combining_marks_wrap(self):
        text = 'hello 世界 e\u0301 example'
        glyphs = [(c, 0, False) for c in text]
        rows = rendering.wrap(glyphs, 8)
        self.assertEqual(''.join(c for row in rows for c, _, _ in row), text)
        self.assertTrue(all(sum(rendering.width(c) for c, _, _ in row) <= 8 for row in rows))

    def test_highlighting_uses_words_and_gaps(self):
        config = dict(settings.DEFAULTS)
        doc = lyrics.parse_lrc('[00:01]<00:01>hello <00:02>world<00:03>')
        with patch('curses.has_colors', return_value=False):
            glyphs = rendering.glyphs(doc, 0, 2.5, config)
            self.assertEqual(''.join(c for c, _, active in glyphs if active), 'world')
            config['word_sync'] = False
            self.assertFalse(any(active for _, _, active in rendering.glyphs(doc, 0, 2.5, config)))

    def test_effects_work_without_colour_support(self):
        config = dict(settings.DEFAULTS, gradient='line', glow='bright', pulse=True)
        with patch('curses.has_colors', return_value=False):
            self.assertIsInstance(rendering.effect_attr(config, .5, 2.3), int)


if __name__ == '__main__':
    unittest.main()

class SparseClockTests(unittest.TestCase):
    def test_limusic_cached_positions_keep_running_between_publications(self):
        clock = PlaybackClock()
        last_publication = 0.0
        errors = []
        for i in range(300):
            elapsed = i / 10
            if elapsed - last_publication >= (2 if int(last_publication) % 2 else 3):
                last_publication = elapsed
            clock.update(observation(20 + last_publication, 100 + elapsed))
            errors.append(abs(clock.seconds(100 + elapsed) - (20 + elapsed)))
        self.assertLess(max(errors), .001)

    def test_sparse_clock_pause_and_seek_are_immediate(self):
        clock = PlaybackClock()
        clock.update(observation(20, 100))
        clock.update(observation(20, 102))
        self.assertAlmostEqual(clock.seconds(102), 22)
        clock.update(observation(22, 102.1, status='Paused'))
        self.assertAlmostEqual(clock.seconds(110), 22)
        clock.update(observation(22, 110))
        clock.update(observation(50, 110.1, seek_serial=1))
        self.assertAlmostEqual(clock.seconds(110.1), 50)

    def test_fresh_but_frozen_player_has_bounded_extrapolation(self):
        clock = PlaybackClock()
        for i in range(200):
            clock.update(observation(20, 100 + i / 10))
        self.assertLessEqual(clock.seconds(120), 24)

class RenderPerformanceTests(unittest.TestCase):
    def test_same_style_text_is_written_as_one_run(self):
        from unittest.mock import MagicMock
        screen = MagicMock()
        screen.getmaxyx.return_value = (24, 100)
        rendering.put_row(screen, 8, [(c, 0, False) for c in 'a complete lyric line'], True)
        self.assertEqual(screen.addstr.call_count, 1)
        self.assertEqual(screen.addstr.call_args.args[2], 'a complete lyric line')
