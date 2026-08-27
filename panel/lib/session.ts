import type { NextRequest } from "next/server";

export function panelRequester(request: NextRequest): string {
  const value = request.headers.get("x-oopz-panel-session") || "missing-session";
  return `panel-${value.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 80)}`;
}
