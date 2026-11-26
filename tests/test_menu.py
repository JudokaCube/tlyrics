"""Exercise real menu navigation without changing the user's preferences."""
import copy
import curses
import unittest
from unittest.mock import MagicMock, patch
import settings
import tlyrics


class MenuTests(unittest.TestCase):
    def run_menu(self, keys, choice=None, size=(24, 100), save_error=None):
        config = copy.deepcopy(settings.DEFAULTS)
        screen = MagicMock()
        screen.getmaxyx.return_value = size
        screen.getch.side_effect = keys
        with patch('curses.has_colors', return_value=False), patch.object(tlyrics, 'apply_theme', return_value=0) as theme, \
             patch.object(tlyrics, 'choose', side_effect=choice) as chooser, \
             patch.object(settings, 'save', side_effect=save_error) as save:
            result = tlyrics.menu(screen, config, [])
        return config, screen, save, chooser, result, theme

    def test_root_only_shows_categories(self):
        config, screen, save, _, _, _ = self.run_menu([27])
        text = ' '.join(str(call.args[2]) for call in screen.addnstr.call_args_list)
        self.assertIn('Playback & lyrics', text)
        self.assertIn('Command aliases', text)
        self.assertNotIn('Gradient target', text)
        save.assert_not_called()

    def test_submenu_back_preserves_draft_and_ctrl_s_saves(self):
        keys = [curses.KEY_DOWN, 10, 10, 27, 19]  # Appearance > Theme > back > save
        config, _, save, chooser, _, theme = self.run_menu(keys, lambda *args: 'green')
        self.assertEqual(config['theme'], 'green')
        save.assert_called_once()
        self.assertEqual(chooser.call_args.args[1], 'Theme')
        self.assertLessEqual(theme.call_count, 3)

    def test_cancel_discards_unsaved_changes(self):
        config, _, save, _, _, _ = self.run_menu([curses.KEY_DOWN, 10, 10, 27, 27], lambda *args: 'green')
        self.assertEqual(config['theme'], settings.DEFAULTS['theme'])
        save.assert_not_called()

    def test_update_check_saves_other_categories(self):
        keys = [curses.KEY_DOWN, 10, 10, 27] + [curses.KEY_DOWN] * 3 + [10, curses.KEY_DOWN, curses.KEY_DOWN, 10]
        config, _, save, _, result, _ = self.run_menu(keys, lambda *args: 'green')
        self.assertEqual(result, 'check_updates')
        self.assertEqual(config['theme'], 'green')
        save.assert_called_once()

    def test_small_terminal_navigation_and_save_failure(self):
        config, screen, save, _, _, _ = self.run_menu([10, 19, 27, 27], size=(8, 25), save_error=OSError('read only'))
        save.assert_called_once()
        self.assertEqual(config, settings.DEFAULTS)

    def test_every_editable_setting_has_exactly_one_category(self):
        keys = [key for rows in tlyrics.SETTINGS_GROUPS.values() for key, _ in rows]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(settings.DEFAULTS) - {'aliases', 'song_offsets'}, set(keys) - set(tlyrics.SETTINGS_GROUPS) - {'preset', 'browser', 'add', 'remove', 'check_updates'})

    def test_invalid_local_file_does_not_create_background_workers(self):
        from types import SimpleNamespace
        screen = MagicMock()
        args = SimpleNamespace(lrc='/missing/tlyrics-test-song.lrc', settings=False)
        with patch('curses.raw'), patch('curses.curs_set'), patch.object(tlyrics, 'apply_theme'), \
             patch.object(tlyrics.concurrent.futures, 'ThreadPoolExecutor') as workers:
            with self.assertRaises(FileNotFoundError):
                tlyrics.ui(screen, MagicMock(), args, copy.deepcopy(settings.DEFAULTS))
            workers.assert_not_called()

    def test_nested_appearance_back_and_save(self):
        keys = [curses.KEY_DOWN, 10] + [curses.KEY_DOWN] * 8 + [10, 10, 27, 27, 19]
        config, _, save, chooser, _, _ = self.run_menu(keys, lambda *args: 'midnight')
        self.assertEqual(config['background'], 'midnight')
        self.assertEqual(chooser.call_args.args[1], 'Background')
        save.assert_called_once()

    def test_visualizer_subcategory_edit(self):
        keys = [curses.KEY_DOWN] * 6 + [10] + [curses.KEY_DOWN] * 5 + [10, 10, 27, 27, 19]
        config, _, save, chooser, _, _ = self.run_menu(keys, lambda *args: 'pipewire')
        self.assertEqual(config['viz_backend'], 'pipewire')
        self.assertEqual(chooser.call_args.args[1], 'Audio backend')
        self.assertFalse(config['visualizer'])

    def test_preset_draft_can_be_cancelled(self):
        keys = [curses.KEY_DOWN, 10] + [curses.KEY_DOWN] * 7 + [10, 27, 27]
        config, _, save, _, _, _ = self.run_menu(keys, lambda *args: 'neon')
        self.assertEqual(config, settings.DEFAULTS)
        save.assert_not_called()
