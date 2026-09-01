import { randomUUID } from "node:crypto";

const DEFAULT_COMMAND_ENDPOINT =
  "http://127.0.0.1:18080/internal/qqbot/command";
const DEFAULT_SNAPSHOT_ENDPOINT =
  "http://127.0.0.1:18080/internal/panel/snapshot";
const DEFAULT_EVENTS_ENDPOINT =
  "http://127.0.0.1:18080/internal/panel/events";

export type BridgeResult = Record<string, unknown> & {
  ok?: boolean;
  message?: string;
};

function bridgeToken(): string {
  const token = process.env.QQBOT_BRIDGE_TOKEN?.trim();
  if (!token) throw new Error("命令桥接尚未配置");
  return token;
}

export async function callBridge(
  command: string,
  requesterId: string,
  commandId = randomUUID(),
  expectedVersion?: number,
): Promise<{
  response: Response;
  result: BridgeResult;
}> {
  const endpoint =
    process.env.OOPZBOT_BRIDGE_URL?.trim() || DEFAULT_COMMAND_ENDPOINT;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-qqbot-bridge-token": bridgeToken(),
    },
    body: JSON.stringify({
      command,
      requester_id: requesterId,
      requester_name: `Web 面板 ${requesterId.slice(-8)}`,
      group_openid: "server-panel",
      command_id: commandId,
      ...(expectedVersion === undefined ? {} : { expected_version: expectedVersion }),
    }),
    cache: "no-store",
    signal: AbortSignal.timeout(15_000),
  });
  const result = (await response.json()) as BridgeResult;
  return { response, result };
}

export async function callSnapshot(): Promise<{
  response: Response;
  result: BridgeResult;
}> {
  const endpoint =
    process.env.OOPZBOT_PANEL_SNAPSHOT_URL?.trim() || DEFAULT_SNAPSHOT_ENDPOINT;
  const response = await fetch(endpoint, {
    headers: { "x-qqbot-bridge-token": bridgeToken() },
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  const result = (await response.json()) as BridgeResult;
  return { response, result };
}

export async function callPanelEvents(lastEventId?: string): Promise<Response> {
  const endpoint =
    process.env.OOPZBOT_PANEL_EVENTS_URL?.trim() || DEFAULT_EVENTS_ENDPOINT;
  return fetch(endpoint, {
    headers: {
      "x-qqbot-bridge-token": bridgeToken(),
      Accept: "text/event-stream",
      ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
    },
    cache: "no-store",
  });
}
