"use strict";

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

// Prevent the upstream module from opening its own listener and checking npm.
process.env.NODE_ENV = "test";
process.chdir(serviceDirectory);
require(path.join(serviceDirectory, "node_modules", "ts-node", "register", "transpile-only"));

async function configureCookie() {
  const cookie = String(process.env.QQ_MUSIC_COOKIE || "").trim();
  if (!cookie) return;

  // The upstream TypeScript service keeps its user config in memory. Populate
  // that config from the container environment before app.ts is loaded, and
  // also set Axios' default Cookie header for its c.y.qq.com/u.y.qq.com calls.
  const config = require(path.join(serviceDirectory, "src", "config", "index.ts"));
  const cookieObject = {};
  for (const part of cookie.split(";")) {
    const separator = part.indexOf("=");
    if (separator <= 0) continue;
    const key = part.slice(0, separator).trim();
    const value = part.slice(separator + 1).trim();
    if (key && value) cookieObject[key] = value;
  }

  const user = config.userInfo || config.appConfig?.user;
  if (user) {
    user.cookie = cookie;
    user.cookieList = Object.entries(cookieObject).map(([key, value]) => `${key}=${value}`);
    user.cookieObject = cookieObject;
    user.uin = cookieObject.uin || user.uin || "";
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

(async () => {
  await configureCookie();
  const loaded = require(path.join(serviceDirectory, "src", "app.ts"));
  const app = loaded.default || loaded;

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
