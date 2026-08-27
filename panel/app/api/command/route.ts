import { NextRequest, NextResponse } from "next/server";
import { callBridge } from "../../../lib/bridge";
import { panelRequester } from "../../../lib/session";

export async function POST(request: NextRequest) {
  try {
    const input = (await request.json()) as { command?: unknown };
    const command = String(input.command || "").trim();
    if (!command || command.length > 150) {
      return NextResponse.json(
        { ok: false, message: "命令内容无效" },
        { status: 400, headers: { "Cache-Control": "no-store" } },
      );
    }

    const requestedCommandId = request.headers.get("x-request-id") || undefined;
    const { response, result } = await callBridge(
      command,
      panelRequester(request),
      requestedCommandId,
    );
    return NextResponse.json(result, {
      status: response.status,
      headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "暂时无法连接 Oopzbot";
    return NextResponse.json(
      { ok: false, message },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
