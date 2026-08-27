import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "oopz_panel_session";

function sameText(left: string, right: string): boolean {
  const length = Math.max(left.length, right.length);
  let difference = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

function challenge(message: string, status = 401): NextResponse {
  return new NextResponse(message, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
      ...(status === 401 ? { "WWW-Authenticate": 'Basic realm="OOPZ Control"' } : {}),
    },
  });
}

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/api/health") return NextResponse.next();

  const username = process.env.OOPZ_PANEL_USERNAME?.trim() || "";
  const password = process.env.OOPZ_PANEL_PASSWORD || "";
  if (!username || !password) return challenge("面板认证尚未配置", 503);

  const authorization = request.headers.get("authorization") || "";
  let suppliedUsername = "";
  let suppliedPassword = "";
  if (authorization.startsWith("Basic ")) {
    try {
      const decoded = atob(authorization.slice(6));
      const separator = decoded.indexOf(":");
      suppliedUsername = separator >= 0 ? decoded.slice(0, separator) : decoded;
      suppliedPassword = separator >= 0 ? decoded.slice(separator + 1) : "";
    } catch {
      // Keep empty values and return the normal authentication challenge.
    }
  }
  if (!sameText(suppliedUsername, username) || !sameText(suppliedPassword, password)) {
    return challenge("需要登录 OOPZ 控制面板");
  }

  const headers = new Headers(request.headers);
  const existingSession = request.cookies.get(SESSION_COOKIE)?.value;
  const session = existingSession || crypto.randomUUID();
  headers.set("x-oopz-panel-session", session);
  const response = NextResponse.next({ request: { headers } });
  if (!existingSession) {
    response.cookies.set(SESSION_COOKIE, session, {
      httpOnly: true,
      sameSite: "strict",
      secure: request.nextUrl.protocol === "https:",
      path: "/",
      maxAge: 60 * 60 * 24 * 30,
    });
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.svg|og.png).*)"],
};
