"""A monotonic playback clock anchored to measured player positions."""
import math
import time


class PlaybackClock:
    def __init__(self):
        self.identity = None
        self.position = None
        self.anchor = 0.0
        self.raw = 0.0
        self.sampled = 0.0
        self.moved = 0.0
        self.status = 'Stopped'
        self.rate = 1.0
        self.duration = 0.0
        self.resolution = 0.001
        self.seek_serial = 0
        self.poll_interval = 0.1

    def update(self, track):
        if track is None or track.position is None:
            self.identity, self.position = None, None
            return
        identity = (track.player, track.key, track.track_id)
        sample = track.sampled
        raw = track.position
        resolution = track.resolution
        if not all(math.isfinite(n) for n in (sample, raw, track.rate, track.duration, resolution)):
            self.identity, self.position = None, None
            return
        reset = (identity != self.identity or track.status != self.status or track.rate != self.rate
                 or track.seek_serial != self.seek_serial)
        if not reset and sample <= self.sampled:
            return  # The browser bridge can return the same observation repeatedly.
        previous_sample, previous_raw = self.sampled, self.raw
        expected = self.seconds(sample)
        self.identity, self.status, self.rate = identity, track.status, track.rate
        self.duration, self.resolution = track.duration, resolution
        self.seek_serial = track.seek_serial
        self.sampled, self.raw = sample, raw
        if reset or self.status != 'Playing' or expected is None:
            # A rounded browser timestamp is uncertain within its one-second bin.
            self.position = raw + (resolution / 2 if resolution >= 0.5 and self.status == 'Playing' else 0)
            self.anchor = self.moved = sample
            return
        delta = raw - previous_raw
        if delta < -0.04 or abs(raw - expected) > max(0.65, resolution * 1.5):
            self.position, self.anchor, self.moved = raw, sample, sample
            return  # Seek: do not slowly chase a discontinuity.
        if abs(delta) < 0.000001:
            return  # Repeated samples must not pull a running clock backwards.
        self.moved = sample
        target = raw
        boundary = False
        if resolution >= 0.5:
            if 0 < delta <= resolution * 1.1 and sample - previous_sample < 0.5:
                # An observed second transition bounds the boundary between the two polls.
                target = raw + (sample - previous_sample) * self.rate / 2
                boundary = True
            elif raw <= expected < raw + resolution:
                target = expected
            else:
                target = raw + resolution / 2
        error = target - expected
        # A small phase correction removes transport jitter without a visible sawtooth.
        correction = error if boundary else error * min(1.0, max(0.15, (sample - previous_sample) * 4))
        self.position, self.anchor = expected + correction, sample

    def seconds(self, now=None):
        if self.position is None:
            return None
        now = time.monotonic() if now is None else now
        value = self.position
        if self.status == 'Playing':
            # Stop extrapolating when a player stalls or the bridge stops reporting.
            until = min(now, self.sampled + max(1.5, self.poll_interval * 2),
                        self.moved + max(0.3, self.poll_interval * 2 + 0.15, self.resolution + 0.2))
            value += max(0.0, until - self.anchor) * self.rate
        return max(0.0, min(value, self.duration) if self.duration > 0 else value)
