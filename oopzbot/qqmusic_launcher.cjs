"use strict";

const fs = require("node:fs");
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

process.chdir(serviceDirectory);

function parseCookie() {
  const cookie = String(process.env.QQ_MUSIC_COOKIE || "").trim();
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

async function configureRainCookie() {
  const { cookie, cookieObject, loginUin } = parseCookie();
  if (!cookie) return;

  // The upstream TypeScript service keeps its user config in memory. Populate
  // that config from the container environment before app.ts is loaded, and
  // also set Axios' default Cookie header for its c.y.qq.com/u.y.qq.com calls.
  const config = require(path.join(serviceDirectory, "src", "config", "index.ts"));

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
  const axiosModule = await import(
    pathToFileURL(path.join(serviceDirectory, "node_modules", "axios", "index.js")).href
  );
  const axios = axiosModule.default || axiosModule;
  axios.defaults.headers.common = axios.defaults.headers.common || {};
  axios.defaults.headers.common.Cookie = cookie;
}

function configureSansenjianCookie() {
  const { cookie, loginUin } = parseCookie();
  const configDirectory = path.resolve(
    process.env.QQ_MUSIC_API_CONFIG_DIR || path.join(serviceDirectory, "config"),
  );
  process.env.QQ_MUSIC_API_CONFIG_DIR = configDirectory;
  if (cookie) process.env.USE_GLOBAL_COOKIE = "true";

  fs.mkdirSync(configDirectory, { recursive: true });
  const serviceConfigPath = path.join(configDirectory, "service-config.json");
  if (!fs.existsSync(serviceConfigPath)) {
    fs.writeFileSync(
      serviceConfigPath,
      JSON.stringify(
        {
          fallbackMode: true,
          useGlobalCookie: Boolean(cookie),
          cookieParamName: "cookie",
        },
        null,
        2,
      ) + "\n",
      { mode: 0o600 },
    );
  }

  const userInfoPath = path.join(configDirectory, "user-info.json");
  if (cookie || !fs.existsSync(userInfoPath)) {
    fs.writeFileSync(
      userInfoPath,
      JSON.stringify({ loginUin, cookie }, null, 2) + "\n",
      { mode: 0o600 },
    );
  }
}

async function loadApplication() {
  const sourceEntry = path.join(serviceDirectory, "src", "app.ts");
  if (fs.existsSync(sourceEntry)) {
    // Prevent the upstream source module from opening its own listener and
    // checking npm when the managed Rain120 installation is used.
    process.env.NODE_ENV = "test";
    require(path.join(serviceDirectory, "node_modules", "ts-node", "register", "transpile-only"));
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

  let closing = false;
  function close() {
    if (closing) return;
    closing = true;
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(1), 5000).unref();
  }

  process.once("SIGINT", close);
  process.once("SIGTERM", close);
})().catch((error) => {
  console.error("QQ Music API launcher failed:", error);
  process.exitCode = 1;
});
