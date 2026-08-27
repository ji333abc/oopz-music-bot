"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type PanelSection = "overview" | "music" | "queue" | "members" | "jm";
type Song = {
  id?: string;
  index?: number;
  name?: string;
  artists?: string;
  album?: string;
  cover?: string;
  platform?: string;
  duration?: number | string;
  durationText?: string;
};
type Playback = Song & {
  playing: boolean;
  paused: boolean;
  loading: boolean;
  progress: number;
  duration: number;
};
type Member = { id: string; name: string; is_bot: boolean };
type VoiceChannel = {
  id: string;
  name: string;
  configured: boolean;
  member_count: number;
  members: Member[];
};
type ComponentHealth = {
  status: "starting" | "ok" | "degraded" | "error" | "offline" | "unknown";
  message: string;
  reason?: string;
  updated_at?: string;
};
type PanelEvent = {
  id: string;
  type: string;
  message: string;
  level: string;
  source: string;
  created_at: string;
};
type JmJob = {
  id: string;
  album_id: string;
  status: string;
  phase: string;
  page_count?: number | null;
  archive_bytes?: number | null;
  error?: string;
  batch_index?: number;
  batch_total?: number;
  started_at: string;
  updated_at: string;
};
type CommandResult = {
  ok?: boolean;
  message?: string;
  songs?: Song[];
  queue_all?: Song[];
};
type ResourceMetric = { total: number; used: number; free: number; percent: number };
type Infrastructure = {
  ok: boolean;
  hostname?: string;
  updatedAt?: string;
  bandwidth?: ResourceMetric | null;
  disk?: ResourceMetric | null;
  memory?: ResourceMetric | null;
};

const emptyPlayback: Playback = {
  name: "",
  artists: "",
  playing: false,
  paused: false,
  loading: false,
  progress: 0,
  duration: 0,
};

const navigationItems: Array<{ id: PanelSection; icon: string; label: string }> = [
  { id: "overview", icon: "⌂", label: "运行概览" },
  { id: "music", icon: "♫", label: "播放控制" },
  { id: "queue", icon: "≡", label: "播放队列" },
  { id: "members", icon: "◎", label: "频道成员" },
  { id: "jm", icon: "↓", label: "JM 任务" },
];

const componentNames: Record<string, string> = {
  internal_api: "控制桥接",
  legacy_core: "旧版核心",
  oopz_websocket: "OOPZ WebSocket",
  oopz_voice: "OOPZ 语音",
  redis: "Redis",
  qqmusic: "QQ 音乐",
  qq_bot: "QQ 机器人",
  uploader: "QQ 上传器",
};
const phaseNames: Record<string, string> = {
  inspecting: "读取元数据",
  downloading: "下载与打包",
  uploading: "上传 QQ 群文件",
  completed: "已完成",
  failed: "失败",
  timeout: "超时",
};

function durationSeconds(value: unknown): number {
  if (typeof value === "number") return Math.max(0, value);
  const text = String(value || "").trim();
  if (!text) return 0;
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text);
  const parts = text.split(":").map(Number);
  if (parts.some((part) => !Number.isFinite(part))) return 0;
  return parts.reduce((total, part) => total * 60 + part, 0);
}

function formatTime(value: number): string {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index > 2 ? 1 : 0)} ${units[index]}`;
}

function formatMoment(value?: string): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function normalizeSong(song: Song): Song {
  return {
    ...song,
    durationText: song.durationText || String(song.duration || ""),
    duration: durationSeconds(song.duration),
  };
}

export default function Home() {
  const [activeSection, setActiveSection] = useState<PanelSection>("overview");
  const [connected, setConnected] = useState(false);
  const [statusMessage, setStatusMessage] = useState("等待首次同步");
  const [lastUpdated, setLastUpdated] = useState("尚未同步");
  const [operator, setOperator] = useState("受保护控制端");
  const [refreshing, setRefreshing] = useState(false);
  const [playback, setPlayback] = useState<Playback>(emptyPlayback);
  const [elapsed, setElapsed] = useState(0);
  const [queue, setQueue] = useState<Song[]>([]);
  const [channels, setChannels] = useState<VoiceChannel[]>([]);
  const [channelError, setChannelError] = useState("");
  const [health, setHealth] = useState<Record<string, ComponentHealth>>({});
  const [events, setEvents] = useState<PanelEvent[]>([]);
  const [jmJobs, setJmJobs] = useState<JmJob[]>([]);
  const [jmEnabled, setJmEnabled] = useState(false);
  const [songQuery, setSongQuery] = useState("");
  const [songResults, setSongResults] = useState<Song[]>([]);
  const [songAction, setSongAction] = useState("");
  const [queueRemoving, setQueueRemoving] = useState<number | null>(null);
  const [toast, setToast] = useState("");
  const [infrastructure, setInfrastructure] = useState<Infrastructure | null>(null);
  const [infrastructureLoading, setInfrastructureLoading] = useState(true);

  const clearRealtimeState = useCallback((message: string) => {
    setConnected(false);
    setStatusMessage(message);
    setPlayback(emptyPlayback);
    setElapsed(0);
    setQueue([]);
    setChannels([]);
    setChannelError("");
    setHealth({});
    setEvents([]);
    setJmJobs([]);
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await fetch("/api/state", { cache: "no-store", credentials: "same-origin" });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(String(data.message || "状态接口不可用"));
      const rawPlayback = data.playback || {};
      const current = rawPlayback.current ? normalizeSong(rawPlayback.current) : {};
      const nextPlayback: Playback = {
        ...emptyPlayback,
        ...current,
        playing: Boolean(rawPlayback.playing),
        paused: Boolean(rawPlayback.paused),
        loading: Boolean(rawPlayback.loading),
        progress: Number(rawPlayback.progress) || 0,
        duration: Number(rawPlayback.duration) || durationSeconds(current.duration),
      };
      setPlayback(nextPlayback);
      setElapsed(nextPlayback.progress);
      setQueue(Array.isArray(data.queue) ? data.queue.map(normalizeSong) : []);
      setChannels(Array.isArray(data.channels) ? data.channels : []);
      setChannelError(String(data.channel_error || ""));
      setHealth(data.health && typeof data.health === "object" ? data.health : {});
      setEvents(Array.isArray(data.events) ? data.events : []);
      setJmJobs(Array.isArray(data.jm_jobs) ? data.jm_jobs : []);
      setJmEnabled(Boolean(data.jm_enabled));
      setOperator(String(data.operator || "受保护控制端"));
      setConnected(true);
      setStatusMessage("实时数据已连接");
      setLastUpdated(new Date(data.updated_at || Date.now()).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    } catch (error) {
      clearRealtimeState(error instanceof Error ? error.message : "无法连接后端");
    } finally {
      setRefreshing(false);
    }
  }, [clearRealtimeState]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const refreshInfrastructure = useCallback(async () => {
    setInfrastructureLoading(true);
    try {
      const response = await fetch("/api/racknerd", { credentials: "same-origin", cache: "no-store" });
      const data = (await response.json()) as Infrastructure;
      if (!response.ok || !data.ok) throw new Error("RackNerd data unavailable");
      setInfrastructure(data);
    } catch {
      setInfrastructure(null);
    } finally {
      setInfrastructureLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshInfrastructure();
    const timer = window.setInterval(refreshInfrastructure, 60 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [refreshInfrastructure]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (playback.playing && !playback.paused && !playback.loading) {
        setElapsed((value) => Math.min(playback.duration || Infinity, value + 1));
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [playback.duration, playback.loading, playback.paused, playback.playing]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const sendCommand = async (command: string): Promise<CommandResult | null> => {
    try {
      const response = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command }),
      });
      const result = (await response.json()) as CommandResult;
      if (!response.ok || !result.ok) throw new Error(result.message || "命令执行失败");
      setToast(result.message || `已执行：${command}`);
      window.setTimeout(refresh, 400);
      return result;
    } catch (error) {
      setToast(error instanceof Error ? error.message : "命令执行失败");
      return null;
    }
  };

  const removeQueueItem = async (index: number) => {
    setQueueRemoving(index);
    const result = await sendCommand(`删除 ${index}`);
    if (Array.isArray(result?.queue_all)) setQueue(result.queue_all.map(normalizeSong));
    setQueueRemoving(null);
  };

  const searchSongs = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const keyword = songQuery.trim();
    if (!keyword) return setToast("请输入歌曲名或歌手");
    setSongAction("search");
    const result = await sendCommand(`搜歌 ${keyword}`);
    setSongResults(Array.isArray(result?.songs) ? result.songs : []);
    setSongAction("");
  };

  const requestSong = async () => {
    const keyword = songQuery.trim();
    if (!keyword) return setToast("请输入歌曲名或歌手");
    setSongAction("direct");
    const result = await sendCommand(`点歌 ${keyword}`);
    if (result?.ok) { setSongQuery(""); setSongResults([]); }
    setSongAction("");
  };

  const selectSong = async (song: Song, fallbackIndex: number) => {
    const index = Number(song.index) || fallbackIndex + 1;
    setSongAction(`select-${index}`);
    const result = await sendCommand(`选歌 ${index}`);
    if (result?.ok) { setSongQuery(""); setSongResults([]); }
    setSongAction("");
  };

  const navigateTo = (section: PanelSection) => {
    setActiveSection(section);
    const target = document.getElementById(`section-${section}`);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
    target?.focus({ preventScroll: true });
  };

  const progress = playback.duration ? Math.min(100, (elapsed / playback.duration) * 100) : 0;
  const queueDuration = useMemo(() => queue.reduce((sum, song) => sum + durationSeconds(song.duration), 0), [queue]);
  const configuredChannel = channels.find((channel) => channel.configured);
  const onlineCount = configuredChannel?.member_count || 0;
  const activeJm = jmJobs.filter((job) => job.status === "running").length;
  const healthEntries = Object.entries(health);
  const unhealthy = healthEntries.filter(([, item]) => item.status !== "ok").length;
  const systemHealthy = connected && healthEntries.length > 0 && unhealthy === 0;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark">O</div><div><strong>OOPZ</strong><span>CONTROL</span></div></div>
        <nav aria-label="主要导航">
          {navigationItems.map((item) => <button key={item.id} type="button" className={`nav-item ${activeSection === item.id ? "active" : ""}`} onClick={() => navigateTo(item.id)}><span>{item.icon}</span>{item.label}</button>)}
        </nav>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={refresh}><span>↻</span>同步状态</button>
          <div className="operator"><div className="avatar">{operator.slice(0, 1).toUpperCase()}</div><div><strong>{operator}</strong><span>Basic Auth 已保护</span></div><i className={connected ? "online" : ""} /></div>
        </div>
      </aside>

      <main className="main-content section-target" id="section-overview" tabIndex={-1}>
        <header className="topbar">
          <div><p className="eyebrow">SYSTEM OVERVIEW</p><h1>机器人控制面板</h1><p>{statusMessage}</p></div>
          <div className="top-actions"><div className={`connection ${connected ? "connected" : "demo"}`}><span />{connected ? "实时在线" : "服务不可用"}<small>{lastUpdated}</small></div><button className="icon-button" aria-label="刷新数据" onClick={refresh} disabled={refreshing}>{refreshing ? "···" : "↻"}</button><button className="settings-button" onClick={refresh} disabled={refreshing}>立即同步</button></div>
        </header>

        <section className="stats-grid" aria-label="运行指标">
          <article className="stat-card"><div className="stat-icon green">●</div><div><span>系统状态</span><strong>{systemHealthy ? "正常" : connected ? "有异常" : "不可用"}</strong><small className={systemHealthy ? "positive" : ""}>{connected ? `${healthEntries.length} 个组件，${unhealthy} 个异常` : "没有使用演示数据"}</small></div></article>
          <article className="stat-card"><div className="stat-icon purple">◉</div><div><span>目标语音频道</span><strong>{configuredChannel?.name || "不可用"}</strong><small>{connected ? `${onlineCount} 位成员在线` : "等待 OOPZ 数据"}</small></div></article>
          <article className="stat-card"><div className="stat-icon amber">♫</div><div><span>待播歌曲</span><strong>{connected ? `${queue.length} 首` : "—"}</strong><small>{queueDuration ? `约 ${Math.ceil(queueDuration / 60)} 分钟` : connected ? "队列为空" : "等待队列数据"}</small></div></article>
          <article className="stat-card"><div className="stat-icon blue">↓</div><div><span>JM 进行中</span><strong>{jmEnabled ? `${activeJm} 项` : "未启用"}</strong><small>{jmJobs.length ? `保留 ${jmJobs.length} 条任务记录` : "暂无任务历史"}</small></div></article>
        </section>

        <section className="health-grid" aria-label="组件健康状态">
          {healthEntries.length ? healthEntries.map(([key, item]) => <article className="health-item" key={key}><i className={`health-dot ${item.status}`} /><div><strong>{componentNames[key] || key}</strong><span>{item.reason || item.message}</span></div><em>{item.status}</em></article>) : <div className="unavailable-strip">组件健康数据不可用</div>}
        </section>

        <section className="dashboard-grid">
          <div className="primary-column">
            <article className="panel now-playing section-target" id="section-music" tabIndex={-1}>
              <div className="panel-heading"><div><span className={playback.playing ? "live-dot" : "idle-dot"} />播放状态</div><button className="text-button" onClick={refresh}>刷新</button></div>
              <div className="player-body">
                <div className="cover-art" style={playback.cover ? { backgroundImage: `url(${String(playback.cover).replace(/^http:/, "https:")})` } : undefined}><span>♫</span><i /></div>
                <div className="track-info">
                  <div className="track-source">{playback.playing ? (playback.platform === "qq" ? "QQ 音乐" : playback.platform || "音乐平台") : "NO ACTIVE TRACK"}</div>
                  <h2>{playback.playing ? playback.name : "当前没有播放"}</h2>
                  <p>{playback.playing ? playback.artists || "未知歌手" : "点歌后会在这里显示真实状态"}{playback.album ? <><span>·</span>{playback.album}</> : null}</p>
                  <div className="progress-track"><div style={{ width: `${progress}%` }} /><i style={{ left: `${progress}%` }} /></div>
                  <div className="time-row"><span>{formatTime(elapsed)}</span><span>{formatTime(playback.duration)}</span></div>
                  <div className="player-controls"><button className="play-button" disabled={!playback.playing} aria-label={playback.paused ? "继续" : "暂停"} onClick={() => sendCommand(playback.paused ? "继续" : "暂停")}>{playback.paused ? "▶" : "Ⅱ"}</button><button aria-label="下一首" disabled={!playback.playing && queue.length === 0} onClick={() => sendCommand("切歌")}>›</button><button className="stop-button" aria-label="停止" disabled={!playback.playing} onClick={() => sendCommand("停止")}>■</button></div>
                </div>
              </div>
            </article>

            <article className="panel queue-panel section-target" id="section-queue" tabIndex={-1}>
              <div className="panel-heading"><div>待播队列 <em>{queue.length}</em></div><button className="text-button" onClick={refresh}>同步队列</button></div>
              <form className="song-picker" onSubmit={searchSongs}>
                <label htmlFor="song-search">添加歌曲（每个浏览器会话独立保留搜索结果）</label>
                <div className="song-search-row"><input id="song-search" value={songQuery} onChange={(event) => setSongQuery(event.target.value)} placeholder="输入歌曲名或歌手" maxLength={100} autoComplete="off" /><button type="submit" disabled={Boolean(songAction)}>{songAction === "search" ? "搜索中…" : "搜索前 10 首"}</button><button type="button" className="direct-song-button" onClick={requestSong} disabled={Boolean(songAction)}>{songAction === "direct" ? "提交中…" : "直接点歌"}</button></div>
                {songResults.length > 0 && <div className="song-results" aria-label="歌曲搜索结果">{songResults.map((song, index) => { const resultIndex = Number(song.index) || index + 1; return <div className="song-result" key={song.id || `${song.name}-${index}`}><div className="result-cover">{song.cover ? <img src={String(song.cover).replace(/^http:/, "https:")} alt="" /> : "♫"}</div><div><strong>{resultIndex}. {song.name}</strong><span>{song.artists}{song.durationText ? ` · ${song.durationText}` : ""}</span></div><button type="button" onClick={() => selectSong(song, index)} disabled={Boolean(songAction)}>{songAction === `select-${resultIndex}` ? "添加中…" : "加入播放"}</button></div>; })}</div>}
              </form>
              <div className="queue-header"><span>#</span><span>歌曲</span><span>来源</span><span>时长</span><span>操作</span></div>
              <div className="queue-list">{!connected ? <div className="empty-state">播放队列数据不可用。</div> : queue.length === 0 ? <div className="empty-state">待播队列为空。</div> : queue.map((song, index) => <div className="queue-row" key={song.id || `${song.name}-${index}`}><span className="queue-index">{String(index + 1).padStart(2, "0")}</span><div className="mini-cover">{song.cover ? <img src={String(song.cover).replace(/^http:/, "https:")} alt="" /> : "♫"}</div><div className="queue-song"><strong>{song.name}</strong><span>{song.artists}</span></div><span className="source-tag">{song.platform === "netease" ? "网易云" : "QQ 音乐"}</span><span className="duration">{song.durationText || formatTime(durationSeconds(song.duration))}</span><button className="queue-remove" type="button" onClick={() => removeQueueItem(index + 1)} disabled={queueRemoving !== null}>{queueRemoving === index + 1 ? "删除中" : "删除"}</button></div>)}</div>
            </article>
          </div>

          <aside className="secondary-column">
            <article className="panel section-target" id="section-members" tabIndex={-1}>
              <div className="panel-heading"><div>OOPZ 语音频道</div><button className="text-button" onClick={refresh}>刷新</button></div>
              <div className="channel-list">{channelError ? <div className="inline-error">{channelError}</div> : channels.length === 0 ? <div className="empty-state">频道成员数据不可用。</div> : channels.map((channel) => <div className={`channel-card ${channel.configured ? "configured" : ""}`} key={channel.id}><div className="channel-title"><strong>{channel.name}</strong><span>{channel.configured ? "目标频道" : `${channel.member_count} 人`}</span></div><div className="member-list">{channel.members.length ? channel.members.map((member) => <span key={member.id} className={member.is_bot ? "bot-member" : ""}><i />{member.name}{member.is_bot ? "（机器人）" : ""}</span>) : <small>当前无人在线</small>}</div></div>)}</div>
            </article>

            <article className="panel quick-actions">
              <div className="panel-heading"><div>快捷控制</div></div>
              <div className="action-grid"><button onClick={() => document.getElementById("song-search")?.focus()}><span>＋</span><strong>添加歌曲</strong><small>搜索或直接点歌</small></button><button onClick={() => sendCommand(playback.paused ? "继续" : "暂停")} disabled={!playback.playing}><span>{playback.paused ? "▶" : "Ⅱ"}</span><strong>{playback.paused ? "继续" : "暂停"}</strong><small>控制当前歌曲</small></button><button onClick={() => sendCommand("切歌")} disabled={!playback.playing && queue.length === 0}><span>↠</span><strong>切歌</strong><small>播放下一首</small></button><button onClick={() => sendCommand("停止")} disabled={!playback.playing}><span>■</span><strong>停止</strong><small>结束播放</small></button></div>
            </article>

            <article className="panel section-target" id="section-jm" tabIndex={-1}>
              <div className="panel-heading"><div>JM 任务 <em>{jmJobs.length}</em></div><button className="text-button" onClick={refresh}>刷新</button></div>
              <div className="job-list">{!jmEnabled ? <div className="empty-state">JM 功能未启用。</div> : jmJobs.length === 0 ? <div className="empty-state">暂无 JM 任务记录。</div> : jmJobs.map((job) => <div className={`job-row ${job.status}`} key={job.id}><div><strong>JM{job.album_id}</strong><span>{phaseNames[job.phase] || job.phase}{job.page_count ? ` · ${job.page_count} 页` : ""}{job.archive_bytes ? ` · ${formatBytes(job.archive_bytes)}` : ""}</span></div><em>{job.batch_total && job.batch_total > 1 ? `${job.batch_index}/${job.batch_total} · ` : ""}{job.status}</em>{job.error ? <small>{job.error}</small> : <small>{formatMoment(job.updated_at)}</small>}</div>)}</div>
            </article>

            <article className="panel activity-panel">
              <div className="panel-heading"><div>真实事件记录</div><button className="text-button" onClick={refresh}>刷新</button></div>
              <div className="activity-list">{events.length === 0 ? <div className="empty-state">暂无事件记录。</div> : events.slice(0, 12).map((event) => <div className="activity" key={event.id}><span className={`activity-icon ${event.level === "error" ? "event-error" : ""}`}>{event.type.slice(0, 1).toUpperCase()}</span><div><strong>{event.message}</strong><small>{event.source} · {formatMoment(event.created_at)}</small></div></div>)}</div>
            </article>

            <article className="system-card racknerd-card">
              <div className="server-head"><div><span>RACKNERD VPS</span><strong>{infrastructure?.hostname || "基础设施监控"}</strong></div><button aria-label="刷新服务器数据" onClick={refreshInfrastructure} disabled={infrastructureLoading}>{infrastructureLoading ? "···" : "↻"}</button></div>
              {infrastructure?.bandwidth ? <><div className="traffic-visual"><div className="traffic-ring" style={{ background: `conic-gradient(var(--green) ${infrastructure.bandwidth.percent}%, #252b28 0)` }}><div><strong>{infrastructure.bandwidth.percent.toFixed(1)}%</strong><span>本月已用</span></div></div><div className="traffic-metrics"><div><span>流量</span><strong>{formatBytes(infrastructure.bandwidth.used)} / {formatBytes(infrastructure.bandwidth.total)}</strong></div><div><span>磁盘</span><strong>{infrastructure.disk ? `${infrastructure.disk.percent.toFixed(1)}%` : "不可用"}</strong></div><div><span>内存</span><strong>{infrastructure.memory ? `${infrastructure.memory.percent.toFixed(1)}%` : "不可用"}</strong></div></div></div><p><span>每小时自动更新</span><span>{formatMoment(infrastructure.updatedAt)}</span></p></> : <div className="server-unavailable"><strong>{infrastructureLoading ? "正在读取服务器数据" : "RackNerd 数据不可用"}</strong><span>{infrastructureLoading ? "请稍候…" : "检查 API 凭据或稍后刷新"}</span></div>}
            </article>
          </aside>
        </section>
      </main>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}
