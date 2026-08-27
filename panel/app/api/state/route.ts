import { NextResponse } from "next/server";
import { callSnapshot } from "../../../lib/bridge";

export async function GET() {
  try {
    const { response, result } = await callSnapshot();
    return NextResponse.json(
      {
        ...result,
        operator: process.env.OOPZ_PANEL_USERNAME?.trim() || "admin",
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
