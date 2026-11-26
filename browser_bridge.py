"""Optional authenticated loopback bridge for browser userscripts."""
import json
import math
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from settings import CONFIG

PORT = 16389


def token():
    path = CONFIG.parent / 'browser-token'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, 'x', opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
            handle.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


def validate(data):
    if not isinstance(data, dict) or data.get('service') not in ('spotify-web', 'youtube-music-web'):
        raise ValueError('Invalid service')
    for key in ('title', 'artist', 'album', 'session'):
        if not isinstance(data.get(key), str) or len(data[key]) > 1000:
            raise ValueError('Invalid metadata')
    for key in ('position', 'duration', 'rate'):
        if type(data.get(key)) not in (float, int) or not math.isfinite(data[key]) or data[key] < 0:
            raise ValueError('Invalid timing')
    if data.get('status') not in ('Playing', 'Paused', 'Stopped'):
        raise ValueError('Invalid status')
    resolution = data.get('resolution', 1 if data['service'] == 'spotify-web' else 0.001)
    if type(resolution) not in (int, float) or not math.isfinite(resolution) or not 0.001 <= resolution <= 1:
        raise ValueError('Invalid timing resolution')
    serial = data.get('seek_serial', 0)
    if type(serial) is not int or not 0 <= serial <= 2**53:
        raise ValueError('Invalid seek serial')
    if 'sampled_at_ms' in data:
        stamp = data['sampled_at_ms']
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or abs(time.time() - stamp / 1000) > 5:
            raise ValueError('Expired browser sample')
    if data['rate'] > 16 or data['duration'] > 604800 or data['position'] > 604800:
        raise ValueError('Invalid playback bounds')
    return data


class Bridge:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()
        secret = token()
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                supplied = self.headers.get('X-Tlyrics-Token', '')
                if self.path != '/playback' or not secrets.compare_digest(supplied.encode('utf-8'), secret.encode('utf-8')):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 16384:
                        raise ValueError('Invalid length')
                    self.connection.settimeout(2)
                    data = validate(json.loads(self.rfile.read(length)))
                    with bridge.lock:
                        now = time.monotonic()
                        bridge.rows = {k: v for k, v in bridge.rows.items() if now - v[1] < 10}
                        if len(bridge.rows) >= 32 and data['session'] not in bridge.rows:
                            raise ValueError('Too many browser sessions')
                        lag = max(0, time.time() - data.get('sampled_at_ms', time.time() * 1000) / 1000)
                        sampled = now - lag
                        previous = bridge.rows.get(data['session'])
                        if previous is None or sampled >= previous[1]:
                            bridge.rows[data['session']] = (data, sampled)
                except (ValueError, OSError):
                    self.send_error(400)
                    return
                self.send_response(204)
                self.end_headers()

        self.server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def read(self):
        with self.lock:
            return [(dict(data), stamp) for data, stamp in self.rows.values() if time.monotonic() - stamp < 5]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def export_script():
    source = Path(__file__).with_name('browser.user.js').read_text()
    target = CONFIG.parent / 'tlyrics.user.js'
    target.write_text(source.replace('__TOKEN__', token()))
    target.chmod(0o600)
    return target
