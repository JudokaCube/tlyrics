// ==UserScript==
// @name         Terminal Lyrics browser bridge
// @namespace    tlyrics
// @version      1.0.0
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
  setInterval(() => {
    if (busy) return;
    const youtube = location.hostname === 'music.youtube.com';
    const media = document.querySelector('video, audio');
    const metadata = navigator.mediaSession?.metadata;
    let title = metadata?.title || '';
    let artist = metadata?.artist || '';
    let position, duration, status, rate = 1;
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
      // Prefer a sub-second slider only when its range is in track seconds.
      if (progress && Math.abs(Number(progress.max) - duration) < 2) position = Number(progress.value);
      status = navigator.mediaSession?.playbackState === 'playing' ? 'Playing' : 'Paused';
    }
    if (!title || !Number.isFinite(position) || !Number.isFinite(duration)) return;
    busy = true;
    GM_xmlhttpRequest({
      method: 'POST', url: 'http://127.0.0.1:16389/playback', timeout: 1500,
      headers: {'Content-Type': 'application/json', 'X-Tlyrics-Token': '__TOKEN__'},
      data: JSON.stringify({service: youtube ? 'youtube-music-web' : 'spotify-web', session,
        title, artist, album: metadata?.album || '', position, duration, status, rate}),
      onload: () => { busy = false; }, onerror: () => { busy = false; }, ontimeout: () => { busy = false; }
    });
  }, 250);
})();
