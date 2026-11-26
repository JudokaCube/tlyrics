import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import settings
import tlyrics
import browser_bridge


class TimingTests(unittest.TestCase):
    def test_clock_advances_without_lyric_changes(self):
        t = tlyrics.Track('spotify', 'Song', 'Artist', '', 200, 10, 'Playing', 1, 100)
        self.assertAlmostEqual(t.seconds(101.3), 11.3)
        self.assertEqual(tlyrics.clock(t.seconds(101.3)), '0:11')
        self.assertEqual(tlyrics.active_line([(0, 'a'), (30, 'b')], t.seconds(101.3)), 0)

    def test_pause_seek_rate_duration_and_missing_position(self):
        t = tlyrics.Track('pear', 'Song', 'Artist', '', 200, 10, 'Paused', 1, 100)
        self.assertEqual(t.seconds(101), 10)
        t.status, t.position, t.sampled, t.rate = 'Playing', 40, 101, 2
        self.assertEqual(t.seconds(102), 42)
        t.position = 199
        self.assertEqual(t.seconds(102), 200)
        t.position = None
        self.assertIsNone(t.seconds(102))

    def test_service_groups(self):
        cases = [('spotify', '', 'spotify'), ('YoutubeMusic', '', 'youtube-music'),
                 ('pear-desktop', '', 'youtube-music'), ('firefox', '', 'browser'),
                 ('chromium', 'https://open.spotify.com/track/abc', 'spotify-web'),
                 ('firefox', 'https://music.youtube.com/watch?v=abc', 'youtube-music-web')]
        for name, url, service in cases:
            self.assertEqual(tlyrics.classify(name, url), service)
        t = tlyrics.Track('firefox', 'Song', 'Artist', '', 200, 10, 'Playing', service='spotify-web')
        self.assertIs(tlyrics.select_player([t], 'spotify'), t)
        self.assertIs(tlyrics.select_player([t], 'browser'), t)
        self.assertIsNone(tlyrics.select_player([t], 'youtube-music'))

    def test_mpris_explicit_position(self):
        players = tlyrics.Players.__new__(tlyrics.Players)
        players.bridge = None
        players.bus = MagicMock()
        players.bus.list_names.return_value = ['org.mpris.MediaPlayer2.spotify']
        props = MagicMock()
        props.GetAll.return_value = {'Metadata': {'xesam:title': 'Song', 'xesam:artist': ['Artist'],
                                                'mpris:length': 200000000},
                                    'Position': 0, 'PlaybackStatus': 'Playing', 'Rate': 1}
        props.Get.return_value = 12345000
        players.dbus = MagicMock()
        players.dbus.DBusException = RuntimeError
        players.dbus.Interface.return_value = props
        row = players.read()[0]
        self.assertEqual(row.position, 12.345)
        props.Get.assert_called_once_with(tlyrics.IFACE, 'Position', timeout=0.5)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = patch.object(settings, 'CONFIG', self.root / 'settings.json')
        self.binary = patch.object(settings, 'BIN', self.root / 'bin')
        self.config.start()
        self.binary.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.binary.stop)

    def test_roundtrip_and_corrupt_settings(self):
        data = settings.load()
        data.update(theme='purple', offset=1.5, interval=0.5)
        settings.save(data)
        self.assertEqual(settings.load(), data)
        settings.CONFIG.write_text('{broken')
        self.assertEqual(settings.load(), settings.DEFAULTS)
        settings.CONFIG.write_text(json.dumps({'offset': float('nan'), 'theme': 'invalid', 'progress': 1}))
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_alias_creation_collision_and_safe_removal(self):
        with patch('shutil.which', return_value=None):
            settings.add_alias('testlyrics')
            path = settings.BIN / 'testlyrics'
            self.assertTrue(os.access(path, os.X_OK))
            with self.assertRaises(ValueError):
                settings.add_alias('testlyrics')
            settings.remove_alias('testlyrics')
            self.assertFalse(path.exists())
            settings.add_alias('testlyrics')
            path.write_text('user modified command')
            with self.assertRaises(ValueError):
                settings.remove_alias('testlyrics')
        for name in ('../oops', 'hello;echo', 'tlyrics', 'a b', ''):
            with self.assertRaises(ValueError):
                settings.add_alias(name)

    def test_bridge_validation(self):
        good = dict(service='spotify-web', session='test', title='Song', artist='Artist',
                    album='', position=10.5, duration=200, rate=1, status='Playing')
        self.assertEqual(browser_bridge.validate(good), good)
        for update in ({'position': float('nan')}, {'position': -1}, {'service': 'unknown'},
                       {'status': 'bad'}, {'title': ['bad']}):
            with self.assertRaises(ValueError):
                browser_bridge.validate(dict(good, **update))

    def test_bad_lyrics_fields(self):
        self.assertFalse(tlyrics.valid_lyrics({'syncedLyrics': 123}))
        self.assertFalse(tlyrics.valid_lyrics([]))
        self.assertTrue(tlyrics.valid_lyrics({'instrumental': True}))

    def test_private_browser_token_and_export(self):
        with patch.object(browser_bridge, 'CONFIG', self.root / 'settings.json'):
            secret = browser_bridge.token()
            self.assertEqual(secret, browser_bridge.token())
            self.assertGreater(len(secret), 30)
            script = browser_bridge.export_script()
            self.assertNotIn('__TOKEN__', script.read_text())
            self.assertIn(secret, script.read_text())
            self.assertEqual(script.stat().st_mode & 0o077, 0)
            self.assertEqual((self.root / 'browser-token').stat().st_mode & 0o077, 0)


if __name__ == '__main__':
    unittest.main()
