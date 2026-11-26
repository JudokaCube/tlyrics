import curses
import unittest
from unittest.mock import MagicMock, patch

import lyrics
import rendering
import settings
import tlyrics
import visualizer


class ResizeTests(unittest.TestCase):
    def test_complete_equal_bars_at_every_terminal_width(self):
        for width in range(1, 257):
            for bands in settings.CHOICES['viz_bars']:
                for gap in settings.CHOICES['viz_gap']:
                    bars = visualizer.bar_geometry(width, bands, gap)
                    self.assertEqual(len({span for _, span in bars}), 1)
                    self.assertTrue(all(0 <= x < x + span <= width for x, span in bars))
                    self.assertTrue(all(b[0] - (a[0] + a[1]) == gap for a, b in zip(bars, bars[1:])))
                    self.assertLessEqual(abs(bars[0][0] - (width - sum(bars[-1]))), 1)

    def test_unicode_headers_and_setting_values_fit_cells(self):
        screen = MagicMock()
        for width in range(3, 90):
            screen.getmaxyx.return_value = (20, width)
            screen.addnstr.reset_mock()
            tlyrics.put(screen, 2, '歌曲 世界 e\u0301 ' * 20, centered=True)
            y, x, text, count, attr = screen.addnstr.call_args.args
            self.assertLessEqual(x + sum(rendering.width(c) for c in text), width - 1)
            self.assertEqual(count, len(text))
            label = tlyrics.setting_label('Automatic sensitivity', 'off', width - 2)
            self.assertLessEqual(sum(rendering.width(c) for c in label), width - 2)
            if width >= 16:
                self.assertTrue(label.endswith('off'))
        self.assertEqual(rendering.clip_text('abc世界e\u0301', 3, tail=True), '界e\u0301')

    def test_lyrics_reflow_stays_in_panel_after_repeated_resizes(self):
        doc = lyrics.parse_lrc('[00:01]<00:01>' + '世界 hello ' * 30 + '<00:03>last\n[00:04]next')
        screen = MagicMock()
        for height, width in [(40, 140), (18, 25), (14, 24), (25, 57), (40, 140)]:
            screen.getmaxyx.return_value = (height, width)
            for spacing in settings.CHOICES['spacing']:
                for layout in settings.CHOICES['layout']:
                    cfg = dict(settings.DEFAULTS, spacing=spacing, layout=layout, visualizer=True)
                    top, bottom, _ = visualizer.region(height, cfg)
                    with patch('curses.has_colors', return_value=False), patch.object(rendering, 'put_row') as put:
                        rendering.draw(screen, doc, 2, cfg, top, bottom)
                    for call in put.call_args_list:
                        self.assertTrue(top <= call.args[1] < bottom)
                        self.assertLessEqual(sum(rendering.width(g[0]) for g in call.args[2]), width - 4)

    def test_prompt_keeps_input_and_shows_tail_through_resize(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (8, 20)
        screen.get_wch.side_effect = list('a' * 30) + [curses.KEY_RESIZE, 'z', '\n']
        self.assertEqual(tlyrics.prompt(screen, 'Path'), 'a' * 30 + 'z')
        screen.clearok.assert_called_once_with(True)
        self.assertTrue(any(call.args[2].endswith('z▏') for call in screen.addnstr.call_args_list))

    def test_chooser_keeps_selection_through_resize(self):
        screen = MagicMock()
        screen.getmaxyx.return_value = (8, 20)
        screen.getch.side_effect = [curses.KEY_DOWN, curses.KEY_RESIZE, 10]
        self.assertEqual(tlyrics.choose(screen, 'Choose', ['one', 'two'], 'one'), 'two')
        screen.clearok.assert_called_once_with(True)
