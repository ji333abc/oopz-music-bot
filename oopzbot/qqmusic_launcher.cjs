"use strict";

const path = require("node:path");

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
