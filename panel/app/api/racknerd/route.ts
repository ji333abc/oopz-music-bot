import { NextResponse } from "next/server";

const RACKNERD_ENDPOINT = "https://nerdvm.racknerd.com/api/client/command.php";

type ResourceMetric = {
  total: number;
  used: number;
  free: number;
  percent: number;
};

function readTag(xml: string, tag: string): string {
  const match = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, "i"));
  return (match?.[1] || "")
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&#039;", "'")
    .trim();
}

function readMetric(xml: string, tag: string): ResourceMetric | null {
  const values = readTag(xml, tag).split(",").map((value) => Number(value.trim()));
  if (values.length < 4 || values.some((value) => !Number.isFinite(value))) return null;
  return {
    total: Math.max(0, values[0]),
    used: Math.max(0, values[1]),
    free: Math.max(0, values[2]),
    percent: Math.min(100, Math.max(0, values[3])),
  };
}

export async function GET() {
  const key = process.env.RACKNERD_API_KEY?.trim();
  const hash = process.env.RACKNERD_API_HASH?.trim();

  if (!key || !hash) {
    return NextResponse.json(
      { ok: false, message: "RackNerd 服务端凭据尚未配置" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const body = new URLSearchParams({
      key,
      hash,
      action: "info",
      status: "true",
      ipaddr: "true",
      hdd: "true",
      mem: "true",
      bw: "true",
    });
    const response = await fetch(RACKNERD_ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/xml,text/xml,text/plain",
      },
      body,
      cache: "no-store",
      signal: AbortSignal.timeout(12_000),
    });
    if (!response.ok) throw new Error("RackNerd upstream request failed");

    const xml = await response.text();
    if (readTag(xml, "status").toLowerCase() !== "success") {
      throw new Error("RackNerd API rejected the request");
    }

    const rawState = readTag(xml, "vmstat") || readTag(xml, "state");
    return NextResponse.json(
      {
        ok: true,
        provider: "RackNerd",
        hostname: readTag(xml, "hostname") || "RackNerd VPS",
        state: rawState.toLowerCase() || "online",
        bandwidth: readMetric(xml, "bw") || readMetric(xml, "bandwidth"),
        disk: readMetric(xml, "hdd"),
        memory: readMetric(xml, "mem") || readMetric(xml, "memory"),
        updatedAt: new Date().toISOString(),
      },
      { headers: { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } },
    );
  } catch {
    return NextResponse.json(
      { ok: false, message: "暂时无法读取 RackNerd 资源数据" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
