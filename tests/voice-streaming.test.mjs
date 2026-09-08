import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const html = fs.readFileSync(new URL('../legacy_oopzbot/src/web/assets/agora_player.html', import.meta.url), 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
function fixture({ hang = false, bufferFail = false } = {}) {
  const elements = [], tracks = [], contexts = [], revoked = [], timers = new Map();
  let buffered = 0, custom = 0, id = 0;
  class Audio {
    constructor() { this.listeners = {}; this.duration = 180; this.currentTime = 0; elements.push(this); }
    addEventListener(name, fn) { this.listeners[name] = fn; }
    removeEventListener(name) { delete this.listeners[name]; }
    play() { this.paused = false; if (!hang) queueMicrotask(() => this.listeners.playing?.()); return Promise.resolve(); }
    pause() { this.paused = true; }
    removeAttribute() { this.src = ''; }
    load() {}
  }
  class AudioContext {
    constructor() { contexts.push(this); }
    createMediaElementSource() { return { connect() {}, disconnect() {} }; }
    createMediaStreamDestination() { return { stream: { getAudioTracks: () => [{}] } }; }
    async resume() {}
    async close() { this.closed = true; }
  }
  function track() {
    const t = { duration: 180, setVolume(v) { this.volume = v; }, close() { this.closed = true; }, on() {}, startProcessAudioBuffer() {} };
    tracks.push(t); return t;
  }
  const context = vm.createContext({
    Audio, AudioContext, Blob, Uint8Array, Number, atob, performance,
    setTimeout(fn) { timers.set(++id, fn); return id; }, clearTimeout(i) { timers.delete(i); },
    URL: { createObjectURL: () => 'blob:audio', revokeObjectURL: u => revoked.push(u) },
    AgoraRTC: {
      createClient: () => ({ setClientRole() {}, async join() { return 1; }, async publish() {}, async unpublish() {}, async leave() {} }),
      createCustomAudioTrack() { custom++; return track(); },
      async createBufferSourceAudioTrack() { buffered++; if (bufferFail) throw new Error('decode'); return track(); },
    },
    WebSocket: class {},
  });
  context.window = context;
  vm.runInContext(source, context);
  vm.runInContext("_wsInstances.push({ readyState: 1, url: 'wss://edge.sd-rtn.com' });", context);
  return { context, elements, tracks, contexts, revoked, timers, counts: () => ({ buffered, custom }) };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('HTTP URL streams without whole-song buffer and preserves controls', async () => {
  const f = fixture(); const w = f.context;
  await w.agoraJoin('app', '', 'channel', 1);
  const result = await w.agoraPlayAudio('https://cdn.invalid/song.mp3'); assert.equal(result.ok, true, result.error);
  assert.deepEqual(f.counts(), { buffered: 0, custom: 1 });
  assert.equal(f.tracks[0].volume, 30);
  assert.equal(w.agoraPause().ok, true);
  assert.equal(f.elements[0].paused, true);
  assert.equal((await w.agoraResume()).ok, true);
  w.agoraSeek(42);
  assert.equal(w.agoraGetCurrentTime(), 42);
  w.agoraSetVolume(65);
  assert.equal(f.tracks[0].volume, 65);
  f.elements[0].onended();
  assert.equal(w.agoraState(), 'finished');
  await w.agoraStopAudio();
  assert.equal(f.elements[0].src, '');
  assert.equal(f.tracks[0].closed, true);
  assert.equal(f.contexts[0].closed, true);
});

test('stop cancels pending URL load without allowing late playback', async () => {
  const f = fixture({ hang: true }); const w = f.context;
  await w.agoraJoin('app', '', 'channel', 1);
  const pending = w.agoraPlayAudio('https://cdn.invalid/slow.mp3');
  await tick();
  assert.equal(f.timers.size, 1);
  await w.agoraStopAudio();
  assert.equal((await pending).ok, false);
  assert.equal(f.timers.size, 0);
  assert.equal(f.tracks[0].closed, true);
  assert.equal(w.agoraState(), 'joined');
});

test('start timeout releases track, network source and audio context', async () => {
  const f = fixture({ hang: true }); const w = f.context;
  await w.agoraJoin('app', '', 'channel', 1);
  const pending = w.agoraPlayAudio('https://cdn.invalid/slow.mp3');
  await tick();
  [...f.timers.values()][0]();
  const result = await pending;
  assert.equal(result.ok, false);
  assert.match(result.error, /timeout/);
  assert.equal(f.elements[0].src, '');
  assert.equal(f.contexts[0].closed, true);
});

test('memory fallback revokes blob URL even when decode fails', async () => {
  const f = fixture({ bufferFail: true });
  await f.context.agoraJoin('app', '', 'channel', 1);
  assert.equal((await f.context.agoraPlayLocal('YXVkaW8=', 'audio/mpeg')).ok, false);
  assert.deepEqual(f.revoked, ['blob:audio']);
});

test('replacing a pending song cannot close the new streaming track', async () => {
  const f = fixture({ hang: true }); const w = f.context;
  await w.agoraJoin('app', '', 'channel', 1);
  const old = w.agoraPlayAudio('https://cdn.invalid/old');
  await tick();
  const next = w.agoraPlayAudio('https://cdn.invalid/new');
  await tick();
  f.elements[1].listeners.playing();
  assert.equal((await old).ok, false);
  assert.equal((await next).ok, true);
  assert.equal(f.tracks[0].closed, true);
  assert.notEqual(f.tracks[1].closed, true);
  assert.equal(w.agoraState(), 'playing');
  await w.agoraStopAudio();
});

test('successful memory fallback keeps playback controls and releases decoded track', async () => {
  const f = fixture(); const w = f.context;
  await w.agoraJoin('app', '', 'channel', 1);
  assert.equal((await w.agoraPlayLocal('YXVkaW8=', 'audio/mpeg')).ok, true);
  assert.deepEqual(f.counts(), { buffered: 1, custom: 0 });
  assert.deepEqual(f.revoked, ['blob:audio']);
  assert.equal(f.tracks[0].volume, 30);
  await w.agoraStopAudio();
  assert.equal(f.tracks[0].closed, true);
});
