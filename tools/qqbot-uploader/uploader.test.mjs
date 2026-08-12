import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  classifyError,
  isRetryable,
  parseArgs,
  uploadWithRetry,
  validateFile,
} from "./uploader.mjs";

test("parseArgs parses the uploader contract", () => {
  assert.deepEqual(
    parseArgs([
      "--group-openid", "group-1",
      "--msg-id", "message-1",
      "--file", "/tmp/a.zip",
      "--name", "JMa.zip",
    ]),
    {
      "group-openid": "group-1",
      "msg-id": "message-1",
      file: "/tmp/a.zip",
      name: "JMa.zip",
    },
  );
  assert.throws(() => parseArgs(["--file"]), /无效参数/);
});

test("classifyError separates permanent and transient errors", () => {
  assert.equal(classifyError(new RangeError("too large")), "size");
  assert.equal(classifyError(new Error("request timeout")), "timeout");
  assert.equal(classifyError(new Error("fetch failed ECONNRESET")), "network");
  assert.equal(classifyError(new Error("缺少 QQBOT_APP_SECRET")), "auth");
  assert.equal(classifyError(new Error("unknown response")), "api");
  assert.equal(isRetryable("network"), true);
  assert.equal(isRetryable("timeout"), true);
  assert.equal(isRetryable("auth"), false);
});

test("validateFile only accepts files under the JM root", () => {
  const testRoot = fs.mkdtempSync(path.join(process.cwd(), ".test-uploads-"));
  const root = path.join(testRoot, "allowed");
  fs.mkdirSync(root);
  const previousRoot = process.env.QQBOT_JM_TEMP_ROOT;
  const previousLimit = process.env.QQBOT_JM_MAX_BYTES;
  try {
    process.env.QQBOT_JM_TEMP_ROOT = root;
    process.env.QQBOT_JM_MAX_BYTES = "1024";
    const validPath = path.join(root, "sample.zip");
    fs.writeFileSync(validPath, Buffer.alloc(32));
    assert.equal(validateFile(validPath).size, 32);

    const oversizedPath = path.join(root, "large.zip");
    fs.writeFileSync(oversizedPath, Buffer.alloc(2048));
    assert.throws(() => validateFile(oversizedPath), RangeError);

    const outsidePath = path.join(testRoot, `outside-${process.pid}.zip`);
    fs.writeFileSync(outsidePath, Buffer.alloc(1));
    try {
      assert.throws(() => validateFile(outsidePath), /不在 JM 临时目录/);
    } finally {
      fs.rmSync(outsidePath, { force: true });
    }
  } finally {
    if (previousRoot === undefined) delete process.env.QQBOT_JM_TEMP_ROOT;
    else process.env.QQBOT_JM_TEMP_ROOT = previousRoot;
    if (previousLimit === undefined) delete process.env.QQBOT_JM_MAX_BYTES;
    else process.env.QQBOT_JM_MAX_BYTES = previousLimit;
    fs.rmSync(testRoot, { recursive: true, force: true });
  }
});

test("uploadWithRetry retries transient failures once", async () => {
  let calls = 0;
  const bot = {
    async sendFile() {
      calls += 1;
      if (calls === 1) throw new Error("fetch failed ECONNRESET");
      return { upload: { file_uuid: "uuid", ttl: 60 } };
    },
  };
  const result = await uploadWithRetry(bot, {}, {}, {}, 0);
  assert.equal(calls, 2);
  assert.equal(result.upload.file_uuid, "uuid");
});

test("uploadWithRetry does not retry permanent failures", async () => {
  let calls = 0;
  const bot = {
    async sendFile() {
      calls += 1;
      throw new Error("缺少 QQBOT_APP_SECRET");
    },
  };
  await assert.rejects(() => uploadWithRetry(bot, {}, {}, {}, 0));
  assert.equal(calls, 1);
});
