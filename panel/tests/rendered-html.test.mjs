import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import test from "node:test";

const username = "panel-test";
const password = "strong-test-password";
const authorization = `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
process.env.OOPZ_PANEL_USERNAME = username;
process.env.OOPZ_PANEL_PASSWORD = password;

async function loadHandler(label) {
  const serverUrl = new URL("../dist/server/index.js", import.meta.url);
  serverUrl.searchParams.set("test", `${label}-${process.pid}-${Date.now()}`);
  const { default: handler } = await import(serverUrl.href);
  return handler;
}

async function request(handler, path = "/", init = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("authorization", authorization);
  return handler(new Request(`http://localhost${path}`, { ...init, headers }));
}

test("protects and renders the real-data control panel shell", async () => {
  const handler = await loadHandler("render");
  const rejected = await handler(new Request("http://localhost/"));
  assert.equal(rejected.status, 401);
  assert.match(rejected.headers.get("www-authenticate") || "", /Basic/);

  const response = await request(handler);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("set-cookie") || "", /oopz_panel_session=/);
  const html = await response.text();
  assert.match(html, /OOPZ Control/);
  assert.match(html, /机器人控制面板/);
  assert.match(html, /id="section-music"/);
  assert.match(html, /id="section-queue"/);
  assert.match(html, /id="section-members"/);
  assert.match(html, /id="section-jm"/);
  assert.match(html, /id="song-search"/);
  assert.match(html, /搜索前 10 首/);
  assert.match(html, /直接点歌/);
  assert.match(html, /真实事件记录/);
  assert.match(html, /性能与故障诊断/);
  assert.match(html, /组件健康数据不可用/);
  assert.match(html, /没有使用演示数据/);
  assert.doesNotMatch(html, /Administrator/);
  assert.doesNotMatch(html, /演示模式/);
  assert.doesNotMatch(html, /上一首/);
  assert.doesNotMatch(html, /音量/);
  assert.doesNotMatch(html, /QQBOT_BRIDGE_TOKEN/);
  const source = await readFile(new URL("../app/page.tsx", import.meta.url), "utf8");
  assert.match(source, /60 \* 60 \* 1000/);
  assert.match(source, /fetch\("\/api\/state"/);
  assert.match(source, /queue\.map/);
  assert.match(source, /new EventSource\("\/api\/events"/);
  assert.doesNotMatch(source, /if \(sseConnected\) return/);
  assert.match(source, /setQueue\(original\)/);
  assert.match(source, /expected_version/);
  const sortable = await readFile(new URL("../components/QueueSortableList.tsx", import.meta.url), "utf8");
  assert.match(sortable, /PointerSensor/);
  assert.match(sortable, /TouchSensor/);
  assert.match(sortable, /KeyboardSensor/);
  assert.match(sortable, /sortableKeyboardCoordinates/);
  assert.match(sortable, /aria-keyshortcuts="Space ArrowUp ArrowDown"/);
  assert.match(source, /清空队列/);
  assert.match(source, /channel\.members\.map/);
  assert.match(source, />磁盘</);
  assert.match(source, />内存</);
});

test("forwards queue versions and exposes a token-free SSE proxy", async () => {
  const commandRoute = await readFile(new URL("../app/api/command/route.ts", import.meta.url), "utf8");
  const eventsRoute = await readFile(new URL("../app/api/events/route.ts", import.meta.url), "utf8");
  const bridge = await readFile(new URL("../lib/bridge.ts", import.meta.url), "utf8");
  assert.match(commandRoute, /expected_version/);
  assert.match(commandRoute, /status: response\.status/);
  assert.match(eventsRoute, /text\/event-stream/);
  assert.match(eventsRoute, /X-Accel-Buffering/);
  assert.doesNotMatch(eventsRoute, /QQBOT_BRIDGE_TOKEN/);
  assert.match(bridge, /Last-Event-ID/);
  assert.match(bridge, /x-qqbot-bridge-token/);
});

test("proxies the unified structured snapshot", async () => {
  const bridge = createServer((request, response) => {
    assert.equal(request.method, "GET");
    assert.equal(request.headers["x-qqbot-bridge-token"], "panel-test-token");
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({
      ok: true,
      playback: {
        current: { id: "now", name: "当前歌曲", artists: "歌手", duration: "03:00" },
        playing: true,
        paused: false,
        loading: false,
        progress: 12,
        duration: 180,
        online_count: 3,
      },
      queue: [
        { index: 1, id: "q1", name: "第一首", artists: "甲", duration: "04:00" },
        { index: 2, id: "q2", name: "第二首", artists: "乙", duration: "05:00" },
      ],
      channels: [{ id: "music", name: "Music", configured: true, member_count: 3, members: [] }],
      health: { oopz: { status: "online", message: "OOPZ SDK 已连接" } },
      events: [],
      jm_jobs: [],
      updated_at: "2026-08-27T10:00:00Z",
    }));
  });
  await new Promise((resolve) => bridge.listen(0, "127.0.0.1", resolve));
  const address = bridge.address();
  assert.ok(address && typeof address === "object");
  process.env.QQBOT_BRIDGE_TOKEN = "panel-test-token";
  process.env.OOPZBOT_PANEL_SNAPSHOT_URL = `http://127.0.0.1:${address.port}/snapshot`;

  try {
    const handler = await loadHandler("state");
    const response = await request(handler, "/api/state");
    assert.equal(response.status, 200);
    const data = await response.json();
    assert.equal(data.playback.current.name, "当前歌曲");
    assert.equal(data.playback.duration, 180);
    assert.equal(data.channels[0].member_count, 3);
    assert.deepEqual(data.queue.map((song) => song.name), ["第一首", "第二首"]);
    assert.equal(data.operator, username);
  } finally {
    delete process.env.QQBOT_BRIDGE_TOKEN;
    delete process.env.OOPZBOT_PANEL_SNAPSHOT_URL;
    await new Promise((resolve, reject) => bridge.close((error) => error ? reject(error) : resolve()));
  }
});

test("assigns independent command requester ids to browser sessions", async () => {
  const requesterIds = [];
  const bridgeResult = {
    ok: true,
    reply_type: "rank_results",
    title: "热歌榜",
    songs: [{ rank: 1, title: "榜单歌曲", artists: "歌手", album_mid: "album-1" }],
  };
  const bridge = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    requesterIds.push(JSON.parse(body).requester_id);
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify(bridgeResult));
  });
  await new Promise((resolve) => bridge.listen(0, "127.0.0.1", resolve));
  const address = bridge.address();
  assert.ok(address && typeof address === "object");
  process.env.QQBOT_BRIDGE_TOKEN = "panel-test-token";
  process.env.OOPZBOT_BRIDGE_URL = `http://127.0.0.1:${address.port}/command`;

  try {
    const handler = await loadHandler("sessions");
    for (const command of ["搜歌 第一位", "搜歌 第二位"]) {
      const root = await request(handler);
      const cookie = (root.headers.get("set-cookie") || "").split(";")[0];
      const response = await request(handler, "/api/command", {
        method: "POST",
        headers: { "content-type": "application/json", cookie },
        body: JSON.stringify({ command }),
      });
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), bridgeResult);
    }
    assert.equal(requesterIds.length, 2);
    assert.notEqual(requesterIds[0], requesterIds[1]);
    assert.match(requesterIds[0], /^panel-/);
  } finally {
    delete process.env.QQBOT_BRIDGE_TOKEN;
    delete process.env.OOPZBOT_BRIDGE_URL;
    await new Promise((resolve, reject) => bridge.close((error) => error ? reject(error) : resolve()));
  }
});
