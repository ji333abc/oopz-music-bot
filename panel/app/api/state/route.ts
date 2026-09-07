import { NextResponse } from "next/server";
import { callSnapshot } from "../../../lib/bridge";

export async function GET() {
  try {
    const { response, result } = await callSnapshot();
    const configuredPoll = Number(process.env.OOPZ_PANEL_SSE_FALLBACK_POLL_SECONDS || 60);
    const fallbackPollSeconds = Number.isFinite(configuredPoll)
      ? Math.min(300, Math.max(10, configuredPoll))
      : 60;
    return NextResponse.json(
      {
        ...result,
        operator: process.env.OOPZ_PANEL_USERNAME?.trim() || "admin",
        sse_fallback_poll_seconds: fallbackPollSeconds,
      },
      {
        status: response.status,
        headers: {
          "Cache-Control": "private, no-store",
          "X-Content-Type-Options": "nosniff",
        },
      },
    );
  } catch (error) {
    return NextResponse.json(
      {
        ok: false,
        message: error instanceof Error ? error.message : "暂时无法连接 Oopzbot",
      },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
