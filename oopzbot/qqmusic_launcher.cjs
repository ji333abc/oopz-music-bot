"use strict";

const fs = require("node:fs");
const http = require("node:http");
const crypto = require("node:crypto");
const { createRequire } = require("node:module");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const serviceDirectory = path.resolve(process.argv[2] || "");
if (!serviceDirectory) {
  throw new Error("QQ Music API service directory is required");
}

const host = process.env.QQ_MUSIC_HOST || "127.0.0.1";
const port = Number(process.env.PORT || "3200");
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error(`Invalid QQ Music API port: ${process.env.PORT}`);
}

const cookieApiPort = Number(process.env.QQ_MUSIC_COOKIE_API_PORT || String(port + 1));
if (!Number.isInteger(cookieApiPort) || cookieApiPort < 1 || cookieApiPort > 65535) {
  throw new Error(`Invalid QQ Music cookie API port: ${process.env.QQ_MUSIC_COOKIE_API_PORT}`);
}
const cookieApiToken = String(
  process.env.QQ_MUSIC_COOKIE_API_TOKEN || process.env.QQBOT_BRIDGE_TOKEN || "",
).trim();
let rainConfig;
let axios;
let sansenjianUserInfoPath;
let isRainService = false;

process.chdir(serviceDirectory);

function parseCookie(value = process.env.QQ_MUSIC_COOKIE || "") {
  const cookie = String(value).trim();
  const cookieObject = {};
  for (const part of cookie.split(";")) {
    const separator = part.indexOf("=");
    if (separator <= 0) continue;
    const key = part.slice(0, separator).trim();
    const value = part.slice(separator + 1).trim();
    if (key && value) cookieObject[key] = value;
  }
  return {
    cookie,
    cookieObject,
    loginUin: cookieObject.uin || cookieObject.puin || "",
  };
}

async function configureRainCookie(value) {
  const { cookie, cookieObject, loginUin } = parseCookie(value);
  if (!cookie) return;

  // The upstream TypeScript service keeps its user config in memory. Populate
  // that config from the container environment before app.ts is loaded, and
  // also set Axios' default Cookie header for its c.y.qq.com/u.y.qq.com calls.
  const config = rainConfig || require(path.join(serviceDirectory, "src", "config", "index.ts"));
  rainConfig = config;

  const user = config.userInfo || config.appConfig?.user;
  if (user) {
    user.cookie = cookie;
    user.cookieList = Object.entries(cookieObject).map(([key, value]) => `${key}=${value}`);
    user.cookieObject = cookieObject;
    user.uin = loginUin || user.uin || "";
    user.loginUin = user.uin;
  }
  if (config.apiConfig?.commonParams && cookieObject.uin) {
    config.apiConfig.commonParams.loginUin = cookieObject.uin;
  }

  // Axios is ESM in the pinned upstream package, so load it dynamically from
  // this CommonJS launcher.
  if (!axios) {
    const axiosModule = await import(
      pathToFileURL(path.join(serviceDirectory, "node_modules", "axios", "index.js")).href
    );
    axios = axiosModule.default || axiosModule;
  }
  axios.defaults.headers.common = axios.defaults.headers.common || {};
  axios.defaults.headers.common.Cookie = cookie;
}

function writeJsonAtomically(target, payload) {
  const temporary = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, JSON.stringify(payload, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(temporary, target);
}

function configureSansenjianCookie(value) {
  const { cookie, cookieObject, loginUin } = parseCookie(value);
  const configDirectory = path.resolve(
    process.env.QQ_MUSIC_API_CONFIG_DIR || path.join(serviceDirectory, "config"),
  );
  process.env.QQ_MUSIC_API_CONFIG_DIR = configDirectory;
  if (cookie) process.env.USE_GLOBAL_COOKIE = "true";

  fs.mkdirSync(configDirectory, { recursive: true });
  const serviceConfigPath = path.join(configDirectory, "service-config.json");
  if (!fs.existsSync(serviceConfigPath)) {
    writeJsonAtomically(serviceConfigPath, {
      fallbackMode: true,
      useGlobalCookie: Boolean(cookie),
      cookieParamName: "cookie",
    });
  }

  sansenjianUserInfoPath = path.join(configDirectory, "user-info.json");
  if (cookie || !fs.existsSync(sansenjianUserInfoPath)) {
    writeJsonAtomically(sansenjianUserInfoPath, { loginUin, cookie });
  }
  // The package reads this shared object at request time. Keep all forms used
  // by its fallback middleware in sync so a request Cookie header cannot
  // continue to override the newly refreshed global value.
  global.userInfo = {
    ...(global.userInfo || {}),
    cookie,
    cookieList: Object.entries(cookieObject).map(([key, item]) => `${key}=${item}`),
    cookieObject,
    uin: loginUin,
    loginUin,
  };
}

async function updateCookie(cookie) {
  if (isRainService) {
    await configureRainCookie(cookie);
  } else {
    configureSansenjianCookie(cookie);
  }
}

function tokenMatches(candidate) {
  if (!cookieApiToken || !candidate) return false;
  const expected = Buffer.from(cookieApiToken);
  const received = Buffer.from(String(candidate));
  return expected.length === received.length && crypto.timingSafeEqual(expected, received);
}

function startCookieApi() {
  const listener = http.createServer(async (request, response) => {
    if (request.method !== "POST" || request.url !== "/internal/cookie") {
      response.writeHead(404).end();
      return;
    }
    if (!cookieApiToken) {
      response.writeHead(503, { "content-type": "application/json" }).end('{"ok":false}');
      return;
    }
    if (!tokenMatches(request.headers["x-qqbot-bridge-token"])) {
      response.writeHead(401, { "content-type": "application/json" }).end('{"ok":false}');
      return;
    }
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 16384) request.destroy();
    });
    request.on("end", async () => {
      try {
        const payload = JSON.parse(body || "{}");
        const cookie = String(payload.cookie || "").trim();
        if (!cookie) throw new Error("cookie is required");
        await updateCookie(cookie);
        response.writeHead(200, { "content-type": "application/json" }).end('{"ok":true}');
      } catch (error) {
        console.error("QQ Music cookie update failed:", error.message);
        response.writeHead(400, { "content-type": "application/json" }).end('{"ok":false}');
      }
    });
  });
  listener.listen(cookieApiPort, host, () => {
    console.log(`QQ Music cookie update API listening on http://${host}:${cookieApiPort}`);
  });
  listener.on("error", (error) => console.error("QQ Music cookie API listener failed:", error));
  return listener;
}

async function loadApplication() {
  const sourceEntry = path.join(serviceDirectory, "src", "app.ts");
  if (fs.existsSync(sourceEntry)) {
    // Prevent the upstream source module from opening its own listener and
    // checking npm when the managed Rain120 installation is used.
    process.env.NODE_ENV = "test";
    require(path.join(serviceDirectory, "node_modules", "ts-node", "register", "transpile-only"));
    isRainService = true;
    await configureRainCookie();
    const loaded = require(sourceEntry);
    return loaded.default || loaded;
  }

  // The legacy native deployment imports @sansenjian/qq-music-api. Keep the
  // same package in Docker so playback behavior remains identical.
  configureSansenjianCookie();
  const requireFromService = createRequire(path.join(serviceDirectory, "package.json"));
  try {
    const loaded = requireFromService("@sansenjian/qq-music-api");
    return loaded.default || loaded.app || loaded;
  } catch (requireError) {
    const entry = requireFromService.resolve("@sansenjian/qq-music-api");
    const loaded = await import(pathToFileURL(entry).href);
    return loaded.default || loaded.app || loaded;
  }
}

(async () => {
  const app = await loadApplication();

  const server = app.listen(port, host, () => {
    console.log(`QQ Music API listening on http://${host}:${port}`);
  });

  server.on("error", (error) => {
    console.error("QQ Music API listener failed:", error);
    process.exitCode = 1;
  });
  const cookieServer = startCookieApi();

  let closing = false;
  function close() {
    if (closing) return;
    closing = true;
    cookieServer.close();
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 5000).unref();
  }

  process.once("SIGINT", close);
  process.once("SIGTERM", close);
})().catch((error) => {
  console.error("QQ Music API launcher failed:", error);
  process.exitCode = 1;
});
