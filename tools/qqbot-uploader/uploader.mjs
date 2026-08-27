#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import {
  QQBot,
  UploadDailyLimitExceededError,
} from "@tencent-connect/qqbot-nodejs";

const MAX_FILE_BYTES = 100 * 1024 * 1024;
const RETRY_DELAY_MS = 10_000;

function writeLog(level, message) {
  process.stderr.write(`[${level}] ${message}\n`);
}

const logger = {
  debug: (message) => writeLog("debug", String(message)),
  info: (message) => writeLog("info", String(message)),
  warn: (message) => writeLog("warn", String(message)),
  error: (message) => writeLog("error", String(message)),
};

export function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error(`无效参数: ${key ?? "<empty>"}`);
    }
    args[key.slice(2)] = value;
  }
  return args;
}

function requireValue(value, label) {
  const normalized = String(value ?? "").trim();
  if (!normalized) {
    throw new Error(`缺少 ${label}`);
  }
  return normalized;
}

function isWithinRoot(filePath, rootPath) {
  const relative = path.relative(rootPath, filePath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function validateFile(rawPath) {
  if (!path.isAbsolute(rawPath)) {
    throw new Error("上传文件必须使用绝对路径");
  }

  const realPath = fs.realpathSync(rawPath);
  const stat = fs.statSync(realPath);
  if (!stat.isFile()) {
    throw new Error("上传目标不是普通文件");
  }

  const configuredRoot = process.env.QQBOT_JM_TEMP_ROOT || "/home/oopzbot/jm-tasks";
  const rootPath = fs.existsSync(configuredRoot)
    ? fs.realpathSync(configuredRoot)
    : path.resolve(configuredRoot);
  if (!isWithinRoot(realPath, rootPath)) {
    throw new Error("上传文件不在 JM 临时目录中");
  }

  const configuredLimit = Number.parseInt(
    process.env.QQBOT_JM_MAX_BYTES || String(80 * 1024 * 1024),
    10,
  );
  const effectiveLimit = Number.isSafeInteger(configuredLimit) && configuredLimit > 0
    ? Math.min(configuredLimit, MAX_FILE_BYTES)
    : 80 * 1024 * 1024;
  if (stat.size > effectiveLimit) {
    throw new RangeError(
      `文件 ${(stat.size / 1024 / 1024).toFixed(1)} MiB，超过 ${(effectiveLimit / 1024 / 1024).toFixed(0)} MiB 上限`,
    );
  }

  return { realPath, size: stat.size };
}

function sanitizeMessage(error) {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/[\r\n]+/g, " ").slice(-500);
}

export function classifyError(error) {
  if (error instanceof UploadDailyLimitExceededError) {
    return "quota";
  }
  if (error instanceof RangeError) {
    return "size";
  }

  const message = sanitizeMessage(error).toLowerCase();
  const status = Number(error?.httpStatus || 0);
  const code = Number(error?.bizCode || 0);

  if (
    code === 40034031 ||
    /msg.?id.*(?:过期|失效|expired|invalid)|消息被去重|回复次数.*(?:上限|超过)/i.test(message)
  ) {
    return "expired";
  }
  if (
    status === 401 ||
    status === 403 ||
    code === 11255 ||
    /invalid.*(?:token|secret)|unauthori[sz]ed|forbidden|missing qqbot|缺少 qqbot|鉴权|认证/.test(message)
  ) {
    return "auth";
  }
  if (/too large|file size|超过.*(?:mib|mb|大小)|entity too large/.test(message)) {
    return "size";
  }
  if (/timeout|timed out|aborterror|超时/.test(message)) {
    return "timeout";
  }
  if (
    status === 408 ||
    status === 429 ||
    status >= 500 ||
    /network|fetch failed|econn|eai_again|socket|cos put failed|网络/.test(message)
  ) {
    return "network";
  }
  return "api";
}

export function isRetryable(errorType) {
  return errorType === "network" || errorType === "timeout";
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export async function uploadWithRetry(
  bot,
  target,
  source,
  options,
  retryDelayMs = RETRY_DELAY_MS,
) {
  let lastError;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      if (attempt > 1) {
        writeLog("warn", "整次上传开始第 2 次尝试");
      }
      return await bot.sendFile(target, source, options);
    } catch (error) {
      lastError = error;
      const errorType = classifyError(error);
      writeLog(
        "error",
        `第 ${attempt} 次上传失败 (${errorType}): ${sanitizeMessage(error)}`,
      );
      if (attempt === 2 || !isRetryable(errorType)) {
        throw error;
      }
      writeLog("warn", `将在 ${retryDelayMs / 1000} 秒后重试`);
      await sleep(retryDelayMs);
    }
  }
  throw lastError;
}

export async function sendFileWithFallback(
  bot,
  target,
  source,
  options,
  retryDelayMs = RETRY_DELAY_MS,
) {
  try {
    return await uploadWithRetry(bot, target, source, options, retryDelayMs);
  } catch (error) {
    if (!target.msgId || classifyError(error) !== "expired") {
      throw error;
    }
    writeLog("warn", "被动文件回复不可用，改用群主动消息上传");
    const { msgId: _passiveMessageId, ...proactiveTarget } = target;
    return uploadWithRetry(bot, proactiveTarget, source, options, retryDelayMs);
  }
}

async function main() {
  try {
    const args = parseArgs(process.argv.slice(2));
    const appId = requireValue(process.env.QQBOT_APP_ID, "QQBOT_APP_ID");
    const appSecret = requireValue(process.env.QQBOT_APP_SECRET, "QQBOT_APP_SECRET");
    const groupOpenid = requireValue(args["group-openid"], "--group-openid");
    const messageId = requireValue(args["msg-id"], "--msg-id");
    const fileArg = requireValue(args.file, "--file");
    const displayName = path.basename(requireValue(args.name, "--name"));
    const { realPath, size } = validateFile(fileArg);

    writeLog(
      "info",
      `准备上传 ${displayName} (${(size / 1024 / 1024).toFixed(1)} MiB)`,
    );

    const bot = new QQBot({
      appId,
      appSecret,
      logger,
      userAgent: "oopz-qqbot-uploader/1.0.0",
    });
    const result = await sendFileWithFallback(
      bot,
      { scope: "group", targetId: groupOpenid, msgId: messageId },
      { localPath: realPath },
      {
        fileName: displayName,
        onProgress: (uploaded, total) => {
          const percent = total > 0 ? ((uploaded / total) * 100).toFixed(1) : "0.0";
          writeLog("info", `上传进度 ${uploaded}/${total} (${percent}%)`);
        },
      },
    );

    process.stdout.write(`${JSON.stringify({
      ok: true,
      fileUuid: result.upload.file_uuid || "",
      ttl: result.upload.ttl || 0,
    })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      ok: false,
      errorType: classifyError(error),
      message: sanitizeMessage(error),
    })}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
