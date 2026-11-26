const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'browser.user.js'), 'utf8');

function browser({youtube = false, media = null, slider = null} = {}) {
  let tick;
  const messages = [];
  const events = {};
  const nodes = {
    '[data-testid="playback-position"]': {textContent: '0:12'},
    '[data-testid="playback-duration"]': {textContent: '3:00'},
    '[data-testid="playback-progressbar"] input': slider,
    'video, audio': media,
  };
  vm.runInNewContext(source, {
    crypto: {randomUUID: () => 'test-session'},
    location: {hostname: youtube ? 'music.youtube.com' : 'open.spotify.com'},
    navigator: {mediaSession: {metadata: {title: 'Test', artist: 'Artist', album: ''}, playbackState: 'playing'}},
    document: {
      querySelector: selector => nodes[selector] || null,
      addEventListener: (name, fn) => { events[name] = fn; },
    },
    setInterval: fn => { tick = fn; },
    GM_xmlhttpRequest: request => { messages.push(JSON.parse(request.data)); request.onload(); },
  });
  return {tick, messages, events};
}

test('Spotify labels are explicitly coarse', () => {
  const b = browser(); b.tick();
  assert.equal(b.messages[0].position, 12);
  assert.equal(b.messages[0].resolution, 1);
  assert.equal(b.messages[0].service, 'spotify-web');
});

test('Spotify precise slider and seeks are reported', () => {
  const b = browser({slider: {max: '180', value: '12.42', step: '.01'}}); b.tick();
  assert.equal(b.messages[0].position, 12.42);
  assert.equal(b.messages[0].resolution, .01);
  b.events.pointerup({target: {closest: () => ({})}}); b.tick();
  assert.equal(b.messages[1].seek_serial, 1);
});

test('YouTube Music uses the media clock and playback rate', () => {
  const b = browser({youtube: true, media: {currentTime: 12.456, duration: 180,
    playbackRate: 1.5, paused: false, addEventListener() {}}});
  b.tick();
  assert.equal(b.messages[0].position, 12.456);
  assert.equal(b.messages[0].rate, 1.5);
  assert.equal(b.messages[0].resolution, .001);
  assert.equal(b.messages[0].service, 'youtube-music-web');
});

test('Spotify prefers a matching real media clock', () => {
  const b = browser({media: {currentTime: 12.987, duration: 180,
    playbackRate: 1, paused: false, addEventListener() {}}}); b.tick();
  assert.equal(b.messages[0].position, 12.987);
  assert.equal(b.messages[0].resolution, .001);
});
