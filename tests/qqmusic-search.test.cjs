const assert = require("node:assert/strict");
const test = require("node:test");
const { createSearchMiddleware } = require("../oopzbot/qqmusic_search.cjs");

function context(query = {}) { return { method: "GET", path: "/getSearchByKey", query }; }
const success = list => ({ ok: true, json: async () => ({
  code: 0, req_1: { code: 0, data: { body: { song: { list } } } },
}) });

test("desktop search preserves legacy envelope, keyword, limit and page", async () => {
  let request;
  const song = { mid: "mid", name: "爱我别走" };
  const middleware = createSearchMiddleware({ getCookie: () => "cookie-value",
    fetchImpl: async (url, options) => { request = { url, ...options }; return success([song]); },
  });
  const ctx = context({ key: "爱我别走", limit: "10", page: "2" });
  await middleware(ctx, () => assert.fail("must intercept old search route"));
  assert.equal(ctx.status, 200);
  assert.deepEqual(ctx.body.response.data.song.list, [song]);
  assert.equal(request.url, "https://u.y.qq.com/cgi-bin/musicu.fcg");
  const payload = JSON.parse(request.body);
  assert.equal(payload.req_1.method, "DoSearchForQQMusicDesktop");
  assert.deepEqual(payload.req_1.param, { query: "爱我别走", search_type: 0, num_per_page: 10, page_num: 2 });
  assert.equal(request.headers.Cookie, "cookie-value");
});

test("cookie is read on every request, including freshly supplied request cookie", async () => {
  let cookie = "old";
  const received = [];
  const middleware = createSearchMiddleware({ getCookie: () => cookie,
    fetchImpl: async (_, options) => { received.push(options.headers.Cookie); return success([]); },
  });
  await middleware(context({ key: "song" }));
  cookie = "refreshed";
  await middleware(context({ key: "song" }));
  const ctx = context({ key: "song" }); ctx.get = () => "request-cookie";
  await middleware(ctx);
  assert.deepEqual(received, ["old", "refreshed", "request-cookie"]);
});

test("upstream HTTP, business and malformed responses are never empty successes", async () => {
  for (const response of [
    { ok: false, status: 500 },
    { ok: true, json: async () => ({ code: 0, req_1: { code: 1000 } }) },
    { ok: true, json: async () => ({ code: 0, req_1: { code: 0, data: {} } }) },
  ]) {
    const middleware = createSearchMiddleware({ getCookie: () => "secret", fetchImpl: async () => response });
    const ctx = context({ key: "song" }); await middleware(ctx);
    assert.equal(ctx.status, 502);
    assert.equal(ctx.body.response, undefined);
    assert.ok(!JSON.stringify(ctx.body).includes("secret"));
  }
});

test("valid empty search remains HTTP 200", async () => {
  const middleware = createSearchMiddleware({ getCookie: () => "", fetchImpl: async () => success([]) });
  const ctx = context({ key: "none" }); await middleware(ctx);
  assert.equal(ctx.status, 200);
  assert.deepEqual(ctx.body.response.data.song.list, []);
});

test("timeout is explicit and does not leak request details", async () => {
  const middleware = createSearchMiddleware({ getCookie: () => "", fetchImpl: async () => {
    throw Object.assign(new Error("secret request"), { name: "TimeoutError" });
  } });
  const ctx = context({ key: "song" }); await middleware(ctx);
  assert.equal(ctx.status, 504);
  assert.ok(!JSON.stringify(ctx.body).includes("secret"));
});

test("other routes pass through and missing keyword does not contact upstream", async () => {
  const middleware = createSearchMiddleware({ getCookie: () => "", fetchImpl: async () => assert.fail("unexpected fetch") });
  let passed = false;
  await middleware({ method: "GET", path: "/getMusicPlay" }, () => { passed = true; });
  assert.equal(passed, true);
  const ctx = context({}); await middleware(ctx);
  assert.equal(ctx.status, 400);
});
