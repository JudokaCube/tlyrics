import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import settings
import tlyrics


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.json'
        patcher = patch.object(settings, 'CONFIG', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_word_sync_and_every_setting_survive_save_reload(self):
        config = settings.load()
        for key, choices in settings.CHOICES.items():
            config[key] = next((v for v in reversed(choices) if v != config[key]), config[key])
        config['word_sync'] = True
        settings.save(config)
        self.assertEqual(settings.load(), config)
        self.assertTrue(settings.load()['word_sync'])

    def test_two_instances_merge_unrelated_edits_and_keep_unknown_fields(self):
        self.path.write_text(json.dumps({'word_sync': False, 'future_option': 'keep me'}))
        first, second = settings.load(), settings.load()
        first['theme'] = 'green'
        settings.save(first)
        second['visualizer'] = True
        settings.save(second)
        current = settings.load()
        self.assertEqual(current['theme'], 'green')
        self.assertTrue(current['visualizer'])
        self.assertFalse(current['word_sync'])
        self.assertEqual(json.loads(self.path.read_text())['future_option'], 'keep me')

    def test_backup_recovers_corrupt_settings(self):
        config = settings.load()
        config['word_sync'] = False
        settings.save(config)
        config['theme'] = 'green'
        settings.save(config)
        self.path.write_text('{broken')
        self.assertFalse(settings.load()['word_sync'])

    def test_failed_atomic_replace_keeps_saved_file(self):
        config = settings.load()
        settings.save(config)
        before = self.path.read_bytes()
        config['theme'] = 'green'
        with patch.object(settings.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                settings.save(config)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.settings*')), [])

    def test_export_import_preserves_values_and_keeps_local_aliases(self):
        source = settings.load()
        source.update(theme='pink', word_sync=False, song_offsets={'a' * 64: .2}, aliases=['localonly'])
        export = Path(self.temp.name) / 'export.json'
        settings.export_file(export, source)
        payload = json.loads(export.read_text())
        self.assertNotIn('aliases', payload['settings'])
        destination = settings.load()
        destination['aliases'] = ['existing']
        loaded = settings.import_file(export, destination)
        self.assertEqual(loaded['aliases'], ['existing'])
        self.assertFalse(loaded['word_sync'])
        self.assertEqual(loaded['theme'], 'pink')
        self.assertEqual(loaded['song_offsets'], {'a' * 64: .2})
        settings.save(loaded)
        self.assertEqual(settings.load(), loaded)
        with self.assertRaises(FileExistsError):
            settings.export_file(export, source)

    def test_partial_legacy_import_preserves_other_preferences(self):
        path = Path(self.temp.name) / 'legacy.json'
        path.write_text(json.dumps({'interval': 1, 'theme': 'amber'}))
        config = settings.load()
        config['word_sync'] = False
        loaded = settings.import_file(path, config)
        self.assertFalse(loaded['word_sync'])
        self.assertEqual(loaded['interval'], 1.0)
        self.assertIsInstance(loaded['interval'], float)

    def test_invalid_import_is_rejected_without_writing(self):
        path = Path(self.temp.name) / 'bad.json'
        config = settings.load()
        settings.save(config)
        before = self.path.read_bytes()
        for payload in ({'word_sync': 'yes'}, {'offset': float('nan')}, {'unknown': 1},
                        {'format': 'tlyrics-settings', 'version': 99, 'settings': {'theme': 'green'}}):
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                settings.import_file(path, config)
            self.assertEqual(self.path.read_bytes(), before)

    def test_escape_delay_is_short_with_arrow_key_decoding_enabled(self):
        from unittest.mock import MagicMock
        screen = MagicMock()
        with patch('curses.set_escdelay') as delay:
            tlyrics.setup_input(screen)
        delay.assert_called_once_with(25)
        screen.keypad.assert_called_once_with(True)
        screen.notimeout.assert_called_once_with(False)

    def test_import_overrides_concurrent_changes_even_if_equal_to_old_view(self):
        current = settings.load()
        settings.save(current)
        other = settings.load()
        other['word_sync'] = False
        settings.save(other)
        path = Path(self.temp.name) / 'import.json'
        path.write_text('{"word_sync": true}')
        imported = settings.import_file(path, current)
        settings.save(imported)
        self.assertTrue(settings.load()['word_sync'])

    def test_concurrent_song_offsets_do_not_replace_other_songs(self):
        first, second = settings.load(), settings.load()
        first['song_offsets']['a' * 64] = .1
        second['song_offsets']['b' * 64] = .2
        settings.save(first)
        settings.save(second)
        self.assertEqual(settings.load()['song_offsets'], {'a' * 64: .1, 'b' * 64: .2})

    def test_cli_export_import_without_desktop_session(self):
        import os
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        config_root = Path(self.temp.name) / 'config'
        env = dict(os.environ, XDG_CONFIG_HOME=str(config_root), DBUS_SESSION_BUS_ADDRESS='unix:path=/nonexistent')
        export = Path(self.temp.name) / 'cli.json'
        subprocess.run([sys.executable, str(root / 'tlyrics.py'), '--export-settings', str(export)],
                       env=env, check=True, capture_output=True, text=True, timeout=5)
        data = json.loads(export.read_text())
        data['settings']['word_sync'] = False
        data['settings']['theme'] = 'green'
        export.write_text(json.dumps(data))
        subprocess.run([sys.executable, str(root / 'tlyrics.py'), '--import-settings', str(export)],
                       env=env, check=True, capture_output=True, text=True, timeout=5)
        saved = json.loads((config_root / 'tlyrics/settings.json').read_text())
        self.assertFalse(saved['word_sync'])
        self.assertEqual(saved['theme'], 'green')
