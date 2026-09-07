export type ComponentHealth = {
  status: "starting" | "ok" | "degraded" | "error" | "offline" | "unknown";
  message: string;
  reason?: string;
  updated_at?: string;
};

export type LatencyMetric = {
  count?: number; success?: number; failure?: number;
  last_ms?: number | null; p50_ms?: number | null; p95_ms?: number | null;
  success_rate?: number | null; result_counts?: Record<string, number>;
};

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

export function healthSnapshot(value: unknown): Record<string, ComponentHealth> {
  return Object.fromEntries(Object.entries(record(value)).map(([key, raw]) => {
    const item = record(raw);
    const status = item.status;
    const health: ComponentHealth = {
      status: status === "starting" || status === "ok" || status === "degraded"
        || status === "error" || status === "offline" ? status : "unknown",
      message: typeof item.message === "string" ? item.message : "状态未知",
    };
    if (typeof item.reason === "string") health.reason = item.reason;
    if (typeof item.updated_at === "string") health.updated_at = item.updated_at;
    return [key, health];
  }));
}

export function metricSnapshot(value: unknown): Record<string, LatencyMetric> {
  return Object.fromEntries(Object.entries(record(value)).map(([key, raw]) => {
    const item = record(raw);
    const metric: LatencyMetric = {};
    for (const field of ["count", "success", "failure"] as const) {
      const number = item[field];
      if (typeof number === "number" && Number.isFinite(number)) metric[field] = number;
    }
    for (const field of ["last_ms", "p50_ms", "p95_ms", "success_rate"] as const) {
      const number = item[field];
      if (number === null || (typeof number === "number" && Number.isFinite(number))) metric[field] = number;
    }
    metric.result_counts = Object.fromEntries(Object.entries(record(item.result_counts))
      .filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1])));
    return [key, metric];
  }));
}

export function cacheSnapshot(value: unknown): Record<string, number | boolean> {
  return Object.fromEntries(Object.entries(record(value)).filter(
    (entry): entry is [string, number | boolean] => typeof entry[1] === "boolean"
      || (typeof entry[1] === "number" && Number.isFinite(entry[1])),
  ));
}

export function snapshotTime(value: unknown): string {
  if (typeof value !== "string" && typeof value !== "number") return "时间未知";
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : "时间未知";
}
