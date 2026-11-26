// ==UserScript==
// @name         Terminal Lyrics browser bridge
// @namespace    tlyrics
// @version      1.1.0
// @description  Send Spotify / YouTube Music playback timing to your local tlyrics app
// @match        https://open.spotify.com/*
// @match        https://music.youtube.com/*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// ==/UserScript==
(() => {
  'use strict';
  const session = crypto.randomUUID();
  const text = selector => document.querySelector(selector)?.textContent?.trim() || '';
  const seconds = value => value.split(':').reduce((total, part) => total * 60 + Number(part), 0);
  let busy = false;
  let seekSerial = 0;
  const watched = new WeakSet();
  document.addEventListener('pointerup', event => {
    if (event.target.closest?.('[data-testid="playback-progressbar"]')) seekSerial++;
  }, true);
  document.addEventListener('keyup', event => {
    if (event.target.closest?.('[data-testid="playback-progressbar"]')) seekSerial++;
  }, true);
  setInterval(() => {
    if (busy) return;
    const youtube = location.hostname === 'music.youtube.com';
    const media = document.querySelector('video, audio');
    const metadata = navigator.mediaSession?.metadata;
    let title = metadata?.title || '';
    let artist = metadata?.artist || '';
    let position, duration, status, rate = 1, resolution = 0.001;
    if (media && !watched.has(media)) {
      watched.add(media);
      media.addEventListener('seeked', () => { seekSerial++; });
    }
    if (youtube) {
      title = text('ytmusic-player-bar .title') || title;
      artist = text('ytmusic-player-bar .byline a') || artist;
      if (!media) return;
      position = media.currentTime;
      duration = media.duration;
      status = media.paused ? 'Paused' : 'Playing';
      rate = media.playbackRate;
    } else {
      title = text('[data-testid="context-item-info-title"]') || title;
      artist = text('[data-testid="context-item-info-artist"]') || artist;
      const progress = document.querySelector('[data-testid="playback-progressbar"] input');
      const elapsed = text('[data-testid="playback-position"]');
      const total = text('[data-testid="playback-duration"]');
      if (!elapsed || !total) return;
      position = seconds(elapsed);
      duration = seconds(total);
      resolution = 1;
      // Prefer the actual media clock, then a seconds-based slider, then the coarse label.
      if (media && duration > 0 && Math.abs(media.duration - duration) < 2) {
        position = media.currentTime;
        rate = media.playbackRate;
        resolution = 0.001;
      } else if (progress && duration > 0 && Math.abs(Number(progress.max) - duration) < 2) {
        position = Number(progress.value);
        resolution = progress.step === 'any' ? 0.01 : Math.min(1, Math.max(0.001, Number(progress.step) || 1));
      }
      status = navigator.mediaSession?.playbackState === 'playing' ? 'Playing' : 'Paused';
    }
    if (!title || !Number.isFinite(position) || !Number.isFinite(duration)) return;
    busy = true;
    GM_xmlhttpRequest({
      method: 'POST', url: 'http://127.0.0.1:16389/playback', timeout: 1500,
      headers: {'Content-Type': 'application/json', 'X-Tlyrics-Token': '__TOKEN__'},
      data: JSON.stringify({service: youtube ? 'youtube-music-web' : 'spotify-web', session,
        title, artist, album: metadata?.album || '', position, duration, status, rate, resolution,
        seek_serial: seekSerial, sampled_at_ms: Date.now()}),
      onload: () => { busy = false; }, onerror: () => { busy = false; }, ontimeout: () => { busy = false; }
    });
  }, 100);
})();
