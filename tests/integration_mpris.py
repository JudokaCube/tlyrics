"""Run with dbus-run-session -- python tests/integration_mpris.py (requires PyGObject)."""
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tlyrics
from timing import PlaybackClock


def serve():
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    class Player(dbus.service.Object):
        def __init__(self, bus):
            super().__init__(bus, tlyrics.PATH)
            self.state = dict(position=10, status='Playing', rate=1)

        @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            return {'Metadata': dbus.Dictionary({'xesam:title': 'Integration test',
                    'xesam:artist': dbus.Array(['Test'], signature='s'),
                    'mpris:length': dbus.Int64(180000000), 'mpris:trackid': dbus.ObjectPath('/test/track')}, signature='sv'),
                    'Position': dbus.Int64(0), 'PlaybackStatus': self.state['status'],
                    'Rate': dbus.Double(self.state['rate'])}

        @dbus.service.method('org.freedesktop.DBus.Properties', in_signature='ss', out_signature='v')
        def Get(self, interface, property):
            return dbus.Int64(self.state['position'] * 1e6)

        @dbus.service.method('test.Player', in_signature='s', out_signature='')
        def Update(self, state):
            self.state.update(json.loads(state))
            self.Seeked(dbus.Int64(self.state['position'] * 1e6))

        @dbus.service.signal(tlyrics.IFACE, signature='x')
        def Seeked(self, position):
            pass

    bus = dbus.SessionBus(mainloop=DBusGMainLoop())
    name = dbus.service.BusName('org.mpris.MediaPlayer2.tlyrics_test', bus)
    player = Player(bus)
    print('ready', flush=True)
    GLib.MainLoop().run()


def check():
    import dbus
    child = subprocess.Popen([sys.executable, __file__, '--serve'], stdout=subprocess.PIPE, text=True)
    players = None
    try:
        assert child.stdout.readline().strip() == 'ready'
        players = tlyrics.Players()
        assert players.signal_loop is not None
        first = tlyrics.select_player(players.read(), 'tlyrics_test')
        assert first.position == 10
        clock = PlaybackClock()
        clock.update(first)
        remote = dbus.Interface(dbus.SessionBus().get_object('org.mpris.MediaPlayer2.tlyrics_test', tlyrics.PATH), 'test.Player')
        players.changed.clear()
        remote.Update(json.dumps(dict(position=10.2, status='Paused')))
        assert players.changed.wait(2), 'Seek signal did not trigger a refresh'
        second = tlyrics.select_player(players.read(), 'tlyrics_test')
        assert second.seek_serial > first.seek_serial
        clock.update(second)
        assert clock.seconds(time.monotonic() + 10) == 10.2
        remote.Update(json.dumps(dict(position=70, status='Playing', rate=2)))
        final = tlyrics.select_player(players.read(), 'tlyrics_test')
        clock.update(final)
        assert abs(clock.seconds(final.sampled + .1) - 70.2) < .001
        print('MPRIS integration passed: explicit positions, live seek signals, pause, and playback rate.')
    finally:
        if players:
            players.close()
        child.terminate()
        child.wait(timeout=3)


if __name__ == '__main__':
    serve() if '--serve' in sys.argv else check()
