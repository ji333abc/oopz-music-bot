import assert from "node:assert/strict";
import test from "node:test";
import { healthSnapshot, metricSnapshot, cacheSnapshot, snapshotTime } from "../lib/snapshot.ts";

test("health data tolerates malformed fields without rendering objects", () => {
  assert.deepEqual(healthSnapshot(null), {});
  assert.deepEqual(healthSnapshot([]), {});
  assert.deepEqual(healthSnapshot({ bot: { status: "unexpected", message: {} } }),
    { bot: { status: "unknown", message: "状态未知" } });
  assert.deepEqual(healthSnapshot({ bot: { status: "ok", message: "在线" } }),
    { bot: { status: "ok", message: "在线" } });
});

test("metrics retain zero and null but discard invalid numeric fields", () => {
  assert.deepEqual(metricSnapshot({ qq: { count: 0, success: "3", last_ms: null,
    p50_ms: Infinity, result_counts: { ok: 2, invalid: {} } } }),
  { qq: { count: 0, last_ms: null, result_counts: { ok: 2 } } });
});

test("cache values preserve false and zero without accepting arrays or objects", () => {
  assert.deepEqual(cacheSnapshot({ enabled: false, hits: 0, misses: 3, bad: {}, nan: NaN }),
    { enabled: false, hits: 0, misses: 3 });
  assert.deepEqual(cacheSnapshot([1, 2]), {});
});

test("timestamp validation does not misrepresent invalid data as current time", () => {
  for (const value of [null, undefined, {}, [], "bad-date", Infinity]) {
    assert.equal(snapshotTime(value), "时间未知");
  }
  assert.notEqual(snapshotTime(0), "时间未知");
  assert.notEqual(snapshotTime("2026-09-08T12:00:00Z"), "时间未知");
});
