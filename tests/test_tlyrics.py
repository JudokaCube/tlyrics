import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
import urllib.error
import tlyrics as app


def track(player='limusic', status='Playing', position=12):
    return app.Track(player, 'Example', 'Artist', 'Album', 180, position, status)


class LyricsTests(unittest.TestCase):
    def setUp(self):
        self.throttle = patch.multiple(app, NEXT_REQUEST=0.0, RETRY_UNTIL=0.0)
        self.throttle.start()
        self.addCleanup(self.throttle.stop)

    def test_multiple_timestamps_and_offset(self):
        self.assertEqual(app.parse_lrc('[ar:Artist]\n[offset:-500]\n[00:02.50][01:03]hello\n[00:05.0]'),
                         [(3.0, 'hello'), (5.5, ''), (63.5, 'hello')])

    def test_seek_back_and_before_first_line(self):
        lines = [(2, 'a'), (5, 'b'), (10, 'c')]
        self.assertEqual([app.active_line(lines, p) for p in [0, 2, 12, 5, 3]], [-1, 0, 2, 1, 0])

    def test_player_selection(self):
        paused = track('spotify', 'Paused')
        playing = track()
        self.assertIs(app.select_player([paused, playing]), playing)
        self.assertIs(app.select_player([paused, playing], 'spotify'), paused)
        self.assertIsNone(app.select_player([playing], 'missing'))
        self.assertIs(app.select_player([playing, track('vlc')], previous='limusic'), playing)
        browser = app.Track('firefox', 'WhatsApp', '', '', 5, 5, 'Paused')
        self.assertIs(app.select_player([browser, paused]), paused)

    def test_cache_can_work_offline(self):
        with TemporaryDirectory() as directory, patch.object(app, 'CACHE', Path(directory)):
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"syncedLyrics":"[00:01]hello"}'
            with patch('urllib.request.urlopen', return_value=response) as request:
                first = app.get_lyrics(track())
                second = app.get_lyrics(track())
                self.assertEqual(first, second)
                self.assertEqual(request.call_count, 1)

    def test_missing_lyrics_not_cached(self):
        with TemporaryDirectory() as directory, patch.object(app, 'CACHE', Path(directory)):
            error = urllib.error.HTTPError('url', 404, 'missing', {}, None)
            with patch('urllib.request.urlopen', side_effect=error):
                self.assertIn('No matching', app.get_lyrics(track())['message'])
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_rate_limit_honours_retry_after(self):
        error = urllib.error.HTTPError('url', 429, 'slow down', {'Retry-After': '30'}, None)
        with patch('urllib.request.urlopen', side_effect=error) as request:
            first = app.fetch_record({'track_name': 'Test', 'artist_name': 'Artist'})
            second = app.fetch_record({'track_name': 'Test', 'artist_name': 'Artist'})
        self.assertIn('rate limit', first['message'])
        self.assertIn('rate limit', second['message'])
        self.assertEqual(request.call_count, 1)

    def test_old_cache_refreshed_for_words_and_kept_offline(self):
        with TemporaryDirectory() as directory, patch.object(app, 'CACHE', Path(directory)):
            old = {'syncedLyrics': '[00:01]cached'}
            cache = Path(directory) / (app.settings.song_key(track()) + '.json')
            cache.write_text(app.json.dumps(old))
            with patch.object(app, 'fetch_record', return_value={'message': 'offline'}):
                self.assertEqual(app.get_lyrics(track()), old)
            with patch.object(app, 'fetch_record', return_value={'lyricsfile': 'version: "1.0"'}):
                self.assertIn('lyricsfile', app.get_lyrics(track()))

    def test_malformed_payload_not_cached(self):
        with TemporaryDirectory() as directory, patch.object(app, 'CACHE', Path(directory)):
            response = unittest.mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"error":"not lyrics"}'
            with patch('urllib.request.urlopen', return_value=response):
                self.assertIn('Invalid', app.get_lyrics(track())['message'])
                self.assertFalse(list(Path(directory).iterdir()))


if __name__ == '__main__':
    unittest.main()
