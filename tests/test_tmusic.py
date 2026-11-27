"""tmusic exposes its own MPRIS identity, even for YouTube stream URLs."""
import unittest
import players
import tlyrics


class TmusicTests(unittest.TestCase):
    def test_native_player(self):
        self.assertEqual(players.classify('org.mpris.MediaPlayer2.tmusic'), 'tmusic')
        self.assertEqual(players.classify('tmusic.instance1','https://music.youtube.com/watch?v=test'),'tmusic')
        self.assertIsNone(players.classify('tmusicfake'))

    def test_selection(self):
        track=tlyrics.Track('tmusic','Song','Artist','Album',200,12,'Playing',1,0,'tmusic')
        self.assertTrue(players.accepted(track))
        self.assertIs(tlyrics.select_player([track],'tmusic'),track)
