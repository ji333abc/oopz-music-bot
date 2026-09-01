import { NextRequest } from "next/server";
import { callPanelEvents } from "../../../lib/bridge";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if ((process.env.OOPZ_PANEL_SSE_ENABLED || "true").toLowerCase() === "false") {
    return new Response("事件流已禁用", { status: 404 });
  }
  try {
    const upstream = await callPanelEvents(
      request.headers.get("last-event-id") || undefined,
    );
    if (!upstream.ok || !upstream.body) {
      return new Response("事件流暂时不可用", { status: upstream.status || 502 });
    }
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream; charset=utf-8",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
  } catch {
    return new Response("事件流暂时不可用", { status: 502 });
  }
}
