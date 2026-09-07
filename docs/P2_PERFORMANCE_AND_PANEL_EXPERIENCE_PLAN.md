# 第五阶段：性能和面板体验（P2）执行计划

> 状态：代码完成，等待测试服务器生产关闭审计（P2-01 至 P2-10 均已实现）
> 执行方式：先建立指标和并发契约，再优化读路径与面板；JM 拆分和运维工具独立成批
> 核心原则：可观测、有限缓存、显式冲突、事件驱动、默认轻量、升级可回滚
> 执行证据：见 `docs/P2_EXECUTION_REPORT.md`；Docker/测试服与 24 小时观察项不得用本地静态测试代替

## 1. 阶段目标

本阶段解决“功能可用，但状态刷新粗放、外部服务质量不可量化、多人操作存在竞态、默认镜像过重、
升级依赖人工拼命令”的问题。优化必须建立在 P0 稳定性基线和 P1 单一业务所有权之上，不得为了
性能重新引入第二套命令、队列或播放逻辑。

完成后应达到以下结果：

- 相同搜歌请求在短时间内复用结果，降低 QQMusic 上游压力，同时保证缓存有界、可失效、可诊断；
- QQMusic 的登录态、接口延迟、请求错误和歌曲可播放率均有脱敏、结构化状态；
- 面板正常工作时由 SSE 推送语义状态变化，不再每 10 秒获取整份快照；
- 面板删除队列项目时携带队列版本，过期操作返回冲突而不是删除错误歌曲；
- 面板待播队列支持鼠标、触摸和键盘拖动排序，成功顺序持久化到真实队列，过期拖动不会移动错误歌曲；
- 面板展示最近播放、结构化失败原因、命令耗时和外部服务延迟趋势；
- 默认 Bot 镜像不安装 JM 下载、压缩和上传依赖；启用 Compose `jm` Profile 才启动独立 JM 服务；
- 提供统一的宿主机运维命令，覆盖诊断、备份、升级、健康验证和可恢复回滚；
- 所有新增状态均有容量上限和脱敏边界，不将 Cookie、refresh key、JWT、播放 URL 或用户原始标识
  写入面板、日志或诊断包。

### 本阶段最重要的判断标准

P2 不以“请求更少”或“面板看起来更实时”作为唯一成功标准。任何优化都必须同时回答：

1. 数据何时失效，如何证明没有展示错误状态；
2. 多个客户端同时操作时，冲突如何被检测和恢复；
3. 外部依赖异常时，状态是降级、失败还是缓存命中；
4. 事件连接中断时，客户端如何重新获得完整事实；
5. 默认安装是否真的不包含 JM 依赖，而不是仅隐藏入口；
6. 升级失败时，是否能在不删除 `data/` 和 Redis 卷的前提下回到旧镜像。

## 2. 前置条件与当前基线

### 2.1 开始 P2 前的门槛

- main 上的 P0/P1 已通过完整 Python、Panel、Uploader 和 Docker 检查；
- 当前生产提交、镜像 ID、Compose 配置和回滚镜像已记录；
- `/healthz`、`/readyz`、Panel 快照和只读播放器状态可信；
- Redis 队列格式与 `data/` 目录已有可验证备份；
- 生产不存在未解释的容器重启、持续重连、重复回复或重复播放；
- P1 中仍保留的旧核心调用方已知，本阶段不得绕过应用服务直接向旧实现增加新业务逻辑。

若上述任一项不满足，只允许补基线、测试和诊断，不进入 SSE 切换、队列版本迁移或自动升级。

### 2.2 当前代码基线

| 能力 | 当前实现 | P2 问题 |
| --- | --- | --- |
| Panel 状态 | `panel/app/page.tsx` 每 10 秒请求 `/api/state` | 空闲时也重复生成和传输整份快照 |
| Panel 后端 | Next.js `/api/state` 调 Bot `/internal/panel/snapshot` | 无事件流、修订号和断线续传语义 |
| 播放进度 | 浏览器本地每秒递增，10 秒快照校准 | 可保留，但状态切换最多延迟 10 秒 |
| 搜歌 | `QQMusic.search/search_many` 每次调用上游 | 重复关键词会重复占用接口和延迟 |
| QQMusic 诊断 | `last_error` 与凭证摘要 | 无端点延迟分布、可播放率和错误分类统计 |
| 队列删除 | Redis Lua 保证一次批量删除原子性 | 客户端基于旧编号提交时仍可能删错新位置 |
| 面板记录 | `OperationsRegistry` 保存组件、事件和 JM 任务 | 无最近播放、命令耗时和外部依赖时间序列 |
| JM 安装 | Dockerfile 固定安装 `.[jm,legacy,qqmusic-login]` 和 Node 上传器 | 即使关闭 JM 也承担构建时间、体积和攻击面 |
| 运维工具 | 已有安全的 `scripts/backup.py`、`restore.py` | 升级、诊断、镜像回滚仍依赖人工组合命令 |

执行前应补充记录：

- Panel 单客户端空闲 1 小时的 `/api/state` 请求数、响应字节数和 Bot 快照生成次数；
- 常用关键词的 QQMusic 搜索延迟、播放 URL 成功率及错误分类；
- Bot、Panel、QQMusic、Redis 的镜像大小、启动时间、内存与 PID 数；
- 当前默认镜像中 JM Python 包、Node 上传器和相关系统依赖的实际占用；
- 现有备份耗时、归档大小、恢复演练耗时和最近一次可用回滚点。

## 3. 范围边界

### 3.1 本阶段包含

- 进程内有界搜歌缓存及其命中、失效和并发合并；
- QQMusic Cookie 状态、接口延迟、可播放率和失败原因聚合；
- Bot 到 Panel 的状态修订号、SSE 事件流、心跳、重连和全量快照恢复；
- 队列版本与乐观并发控制；
- 待播队列的原子重排，以及 Panel 鼠标、触摸和键盘拖动排序；
- 最近播放、失败记录、命令耗时、外部服务延迟的有界存储和面板展示；
- JM 任务执行从音乐 Bot 进程迁入可选 Compose Profile；
- 核心镜像与 JM 镜像的依赖、构建和运行隔离；
- 宿主机侧诊断、备份、升级、验证和回滚命令；
- 性能、容量、并发、安全、断线恢复和升级失败测试。

### 3.2 本阶段不包含

- 不引入 Prometheus、Grafana、OpenTelemetry Collector 或外部时序数据库；
- 不把 SSE 扩展成双向控制通道，写操作仍使用现有鉴权 POST 接口；
- 不用 WebSocket 代替 SSE；当前需求是单向状态推送；
- 不改 OOPZ WebSocket、Agora 推流或 QQ SDK 底层协议；
- 不进行破坏性的 Redis 队列格式迁移，不要求清空 Redis 或 `data/`；
- 不把 Docker socket 挂载进 Bot、Panel 或 JM 容器；
- 不在自动升级中静默覆盖本地修改、`.env`、Nginx 配置或未知数据目录；
- 不长期保存原始命令文本、用户 OpenID、歌曲播放 URL、Cookie 或 refresh key；
- 不以缓存掩盖上游认证失败、超时或结构变化。

### 3.3 明确禁止的实施方式

- 禁止使用无上限字典保存搜索结果、延迟样本、事件或失败记录；
- 禁止缓存网络错误、401/403、解析异常或空播放 URL，并把它们伪装成正常空结果；
- 禁止让 Panel 根据旧队列编号自动重试删除；冲突后必须刷新并由用户重新确认；
- 禁止只在浏览器内调整队列数组；拖动结果必须通过 QueueService 写入真实队列并受版本保护；
- 禁止每秒通过 SSE 推送完整播放进度；进度继续由客户端插值，服务端只推语义变化与校准；
- 禁止让每个 SSE 客户端拥有无界消息队列；慢客户端只能保留最新状态；
- 禁止仅给现有 Bot 服务加 `profiles: [jm]` 就宣称完成 JM 拆分；默认 Bot 镜像必须实际不含 JM 依赖；
- 禁止自动回滚顺带恢复旧数据；代码/镜像回滚与数据恢复必须是两个显式动作；
- 禁止诊断命令打印 Compose 展开后的 Secret、完整环境变量或凭证文件内容。

## 4. 不可变设计决策

### 4.1 缓存是加速层，不是事实源

- 搜索缓存放在应用/基础设施边界，不进入领域 DTO，也不写入 Redis；
- 默认使用进程内 TTL + LRU，避免为单实例部署引入新的共享缓存复杂度；
- 缓存条目必须是解析后的展示数据副本，调用方不能修改缓存对象；
- 播放 URL、Cookie 状态和认证失败不得进入搜索缓存；
- 重启丢失缓存是正常行为，不能影响功能正确性。

### 4.2 QQMusic 指标按“用户操作”计数

- 接口延迟按端点和结果类别统计；
- 可播放率以一次歌曲播放地址解析请求为一次样本，主音质与回退音质不能重复扩大分母；
- `unplayable`、`cookie_invalid`、`upstream_http`、`timeout`、`network`、`parse_error` 必须区分；
- 所有滚动样本有固定窗口，重启清零允许；最近失败记录需要持久化时只保存结构化脱敏摘要。

### 4.3 SSE 只传状态，不执行命令

- 浏览器连接 Panel 的同源 `/api/events`；Panel 再连接 Bot 内部事件端点；
- 首次连接和无法续传时发送完整 `snapshot`；后续发送带修订号的状态事件；
- 断线时 EventSource 自动重连，连接恢复前保留手动刷新；
- SSE 不携带 Bridge Token 到浏览器，不绕过 Panel Basic Auth；
- Nginx/代理必须关闭缓冲并保留心跳，不能把内部 18080 暴露到公网。

### 4.4 队列版本是并发令牌

- 每个 area 使用独立、单调递增的 `queue_version`；无 area 时使用全局版本；
- 所有改变待播编号的操作必须在同一 Redis 原子操作中修改队列并递增版本；
- Panel 删除、批量删除和清空必须携带 `expected_version`；版本不一致返回 HTTP 409 和最新快照；
- QQ/OOPZ 文本命令没有长期 UI 快照，可继续执行，但也必须触发版本递增；
- Bot 进程启动或兼容旧版本后重新上线时至少推进一次版本，令旧浏览器快照失效；
- 版本字段向后兼容：旧客户端可忽略，新服务不能要求清空旧 Redis 数据。

### 4.5 运维编排在宿主机，不在业务容器

- 统一入口建议为仓库根目录 `oopzctl`/`oopzctl.ps1` 或等价的标准库脚本；
- 宿主机工具调用现有备份/恢复模块和 Docker/Git CLI；
- Bot、Panel、JM 容器不挂载 Docker socket，也不获得宿主机 Git 写权限；
- 升级默认先备份、再构建、再切换、再健康验证；失败只回滚镜像和代码引用；
- 数据恢复必须单独指定归档、组件和 `--confirm`。

### 4.6 队列拖动是版本化写操作

- 当前正在播放的歌曲不属于待播编号，不允许拖动；目标位置表示移动完成后的最终一基位置；
- 拖动只产生一次服务端写入，Panel 可先乐观显示，但成功后必须采用服务端返回的完整队列；
- Redis 使用单次原子操作完成版本校验、位置校验、LIST 重排和版本递增；内存降级实现保持相同语义；
- 拖动期间若自动出队、点歌、删除或另一个客户端已修改队列，服务端返回 `queue_conflict` 和最新队列，
  Panel 回滚/刷新并提示用户重新确认，不按旧编号自动重试；
- 每个待播实例必须有本次快照内唯一的拖动标识，同一歌曲重复入队时不能因歌曲 ID 相同而拖错条目；
- Panel 使用显式拖动手柄，删除按钮和歌曲内容点击不能误触拖动；鼠标、触摸和键盘路径具有同等业务语义。

## 5. 目标数据流

```text
QQ / OOPZ / Panel 命令
          │
          ▼
    CommandService ── command timing / result ──► Metrics + OperationsRegistry
          │
          ├── QueueService ── versioned Redis mutation ──► queue_version
          │       ▲
          │       └── Panel drag reorder (from/to + expected_version)
          │
          └── PlaybackService ──► recent playback / structured failures

QQMusic Adapter
    ├── SearchCache (bounded TTL/LRU, request coalescing)
    └── Endpoint Metrics (latency/error/playability, bounded window)

State Revision Publisher
    ├── semantic state changes
    ├── coalesced latest snapshot
    └── heartbeat
          │
          ▼
Bot internal SSE ──► Panel /api/events ──► Browser EventSource
                                           │
                                           └── POST commands carry queue_version

Optional path:
QQ JM command ──► Redis job contract ──► jm-worker profile ──► result/status event
```

## 6. 总体任务表

| ID | 任务 | 优先级 | 规模 | 依赖 | 主要产物 |
| --- | --- | --- | --- | --- | --- |
| P2-01 | 性能基线、指标契约和容量边界 | P2 | M | P0/P1 | 基线报告、DTO、指标窗口、测试时钟 |
| P2-02 | 搜歌短期缓存 | P2 | M | P2-01 | TTL/LRU Cache、并发合并、缓存指标 |
| P2-03 | QQMusic Cookie、延迟和可播放率诊断 | P2 | L | P2-01 | 诊断服务、错误分类、Panel 状态字段 |
| P2-04 | 队列版本和乐观并发控制 | P2 | L | P2-01 | queue_version、原子脚本、409 合约 |
| P2-05 | Bot/Panel SSE 状态流 | P2 | L | P2-01、P2-04 | 修订发布器、SSE 路由、重连与回退 |
| P2-06 | 面板性能与诊断体验 | P2 | L | P2-03、P2-04、P2-05 | 最近播放、失败、耗时、延迟 UI |
| P2-07 | JM Compose Profile 与镜像拆分 | P2 | XL | P1 稳定边界 | core/jm 镜像、任务协议、Profile |
| P2-08 | 一键升级、备份、回滚和诊断 | P2 | L | P2-03、P2-07 | 宿主机 CLI、升级清单、诊断包 |
| P2-09 | 性能门禁、灰度和最终验收 | P2 | M | 前述全部 | 基准对比、故障演练、发布报告 |
| P2-10 | Panel 待播队列拖拽排序 | P2 | M | P2-04 | 原子 move、拖动 UI、冲突恢复、无障碍测试 |

不得把 P2-04、P2-05 和 P2-07 放进同一个提交：它们分别改变数据并发、长连接和进程边界，
必须可以独立灰度和回滚。

## 7. 详细任务

### P2-01：性能基线、指标契约和容量边界

#### 实施内容

1. 定义不依赖 FastAPI、Redis、requests 或 Panel 的指标 DTO：
   - `LatencySummary`：count、success、failure、last_ms、p50_ms、p95_ms；
   - `ExternalCallResult`：service、operation、result_kind、duration_ms；
   - `CommandTiming`：command_id、source、kind、ok、error_kind、duration_ms、created_at；
   - `PlaybackHistoryItem`：歌曲展示字段、来源、结果、开始/结束时间、失败类型；
   - `PanelStateRevision`：schema_version、revision、generated_at。
2. 使用 `time.monotonic()` 测量耗时，UTC 时间只用于展示和持久化。
3. 建立有界滚动窗口；建议默认：
   - 每个外部端点最近 200 个耗时样本；
   - 最近播放 50 条；
   - 最近命令耗时 200 条；
   - 最近结构化失败 100 条；
   - Panel 事件继续有固定上限，不得因 SSE 改为无限日志。
4. 指标聚合提供 Fake Clock，单元测试不得 `sleep`。
5. Panel 状态 schema 从当前版本演进，旧 `panel-state.json` 加载时填充默认值并原子重写。
6. 给所有新字段建立脱敏测试和 JSON 大小上限测试。

#### 验收标准

- 指标模块可以在不导入网络、数据库和 UI 框架的情况下测试；
- 窗口达到上限后内存和持久化文件不继续增长；
- p50/p95、成功率和可播放率在空样本、小样本和混合错误下定义明确；
- 状态文件从旧 schema 升级不丢失现有组件、事件和 JM 记录；
- 任何 Secret 或原始播放 URL 都不能进入快照和诊断包。

### P2-02：搜歌短期缓存

#### 建议设计

- 默认 TTL：60 秒，可配置范围 5–600 秒；
- 默认容量：256 个 key，可配置范围 16–2048；
- key：平台、规范化关键词、limit、offset/page，以及会影响展示结果的显式参数；
- 关键词规范化只做 `strip` 和连续空白折叠，不擅自改大小写或繁简；
- 正常空搜索可使用较短负缓存（建议 10 秒）；
- 网络、认证、HTTP、超时和解析错误不缓存；
- 相同 key 并发请求只允许一个上游调用，其余等待同一结果；不同 key 不互相阻塞；
- 命中返回不可变对象或深拷贝，避免调用方给缓存结果补 URL 后污染后续请求。

#### 实施内容

1. 在 Music Platform Adapter 外建立 `SearchCache`，不要把缓存逻辑散进 QQ/Panel/OOPZ 入口。
2. 提供 `hit`、`miss`、`coalesced`、`negative_hit`、`eviction`、`upstream_error` 计数。
3. Cookie 刷新不要求清空纯搜索结果；平台配置、API base URL 或解析版本变化时应清空。
4. 缓存关闭时必须与当前行为完全一致。
5. 给 QQ、Panel、OOPZ 共用的搜索服务接入同一缓存，禁止各入口各建一份。

#### 验收标准

- TTL、LRU 淘汰、负缓存、错误不缓存和并发合并均有 Fake Clock/并发测试；
- 60 秒内连续相同搜索只产生一次上游请求；
- 缓存命中不会复用播放 URL，也不会隐藏刚发生的认证失败；
- 容量达到上限后内存稳定；
- 开启缓存后命令结果顺序、字段和选歌会话语义保持兼容。

### P2-03：QQMusic Cookie、延迟和可播放率诊断

#### 指标定义

| 指标 | 定义 | 不计入的情况 |
| --- | --- | --- |
| Cookie 状态 | missing / valid / expiring / expired / refresh_failed | 不展示 Cookie 内容和 refresh key |
| 接口可用率 | 完成有效 HTTP/JSON 处理的请求数 ÷ 总请求数 | 主动取消 |
| 可播放率 | 最终获得非空播放 URL 的歌曲请求数 ÷ 完成播放解析的歌曲请求数 | 网络/超时单独归为依赖失败 |
| 接口延迟 | 每个端点最近窗口的 last/p50/p95 | 缓存命中不冒充上游延迟 |
| 缓存命中率 | hit ÷ (hit + miss) | 被合并等待可单列 coalesced |

#### 实施内容

1. 在 QQMusic `_get` 边界记录端点、耗时、HTTP 状态类别、超时、网络和 JSON 解析结果。
2. 在一次 `get_song_url` 用户操作完成后记录最终 `playable/unplayable/dependency_error`，多音质回退只算一次。
3. 将 401/403 映射为 `cookie_invalid`，空 URL 映射为 `unplayable`，两者不能混为“接口挂了”。
4. 复用现有 `credential_status()` 输出脱敏登录态和到期时间；增加最近刷新结果与下一次检查时间时，
   不得持久化 refresh key。
5. `/readyz` 继续只表达能否处理业务；详细指标放 Panel snapshot/diagnostics，不能因短期 p95 上升让容器重启。
6. 面板展示最近窗口与样本数；样本太少时显示“数据不足”，不展示误导性百分比。

#### 验收标准

- 200 空 URL、401、404、超时、连接失败、非法 JSON 和回退音质成功均有测试；
- `last_error` 与新结构化诊断保持用户错误文案兼容；
- 健康状态、延迟和可播放率三个概念互不覆盖；
- 面板和诊断输出不包含 Cookie、Authorization、refresh key、完整 UIN 或播放 URL；
- 诊断采集本身不会额外请求每首歌曲或显著放大 QQMusic 流量。

### P2-04：队列版本和乐观并发控制

#### 数据契约

- 新 Redis key：全局 `music:queue_version`；有 area 时为 `music:{area}:queue_version`；
- 值为非负整数，旧数据不存在时从 0 初始化；
- `QueueSnapshot` 增加 `version`；Panel snapshot 和队列写接口均返回该值；
- Panel 写请求增加 `expected_version`；缺失时仅兼容旧客户端，不作为新 UI 的正常路径；
- 版本冲突返回：

```json
{
  "ok": false,
  "code": "queue_conflict",
  "message": "队列已被其他操作更新，请确认最新顺序后重试",
  "expected_version": 12,
  "actual_version": 14,
  "queue": []
}
```

#### 实施内容

1. 枚举所有改变待播编号的路径：enqueue、next/pop、remove、remove_many、clear、随机弹出和旧 Web 命令。
2. Redis Adapter 使用 Lua 或 WATCH/MULTI，在同一原子操作中：
   - 校验可选 `expected_version`；
   - 校验位置；
   - 修改 LIST；
   - `INCR queue_version`；
   - 返回新版本和变更结果。
3. In-Memory Adapter 实现相同版本语义和锁边界。
4. 进程启动或首次接管旧数据时推进版本，使重启前浏览器快照失效。
5. Panel 删除冲突后更新到服务端返回的最新队列，突出显示冲突，不自动对新编号重试。
6. QQ/OOPZ 命令保持原文案，但其变更必须立即反映到 Panel 版本和 SSE。

#### 验收标准

- 两个客户端读取同一版本后，只有第一个删除成功，第二个得到 409 且没有删除其他歌曲；
- 校验失败、越界和版本冲突都发生在任何修改之前；
- 所有队列变更路径都递增版本，架构测试能阻止绕过 Adapter 的直接 LIST 写入；
- 旧 Redis 队列可直接读取，旧版本回滚后忽略额外版本 key；
- area 隔离正确，一个 area 的变更不会让另一个 area 无故冲突。

### P2-05：Bot/Panel SSE 状态流

#### 协议建议

- Bot 内部端点：`GET /internal/panel/events`；
- Panel 同源端点：`GET /api/events`；
- MIME：`text/event-stream`，响应头包含 `Cache-Control: no-store`、`X-Accel-Buffering: no`；
- 事件类型：`snapshot`、`state`、`heartbeat`、`reset`；
- 每个数据事件包含 `schema_version`、`revision`、`generated_at`；
- 心跳建议 15–25 秒，不重新计算完整快照；
- 浏览器使用 `Last-Event-ID`；无法补齐时发送 `reset` 后全量 `snapshot`。

#### 实施内容

1. 建立进程内 `StatePublisher`，仅在语义状态变化时递增 revision。
2. 高频变更合并：短时间内多个组件更新只发布最后一份快照；慢客户端队列容量设为 1，丢弃旧快照。
3. 播放进度继续由浏览器本地计时；播放、暂停、继续、切歌、停止、队列或健康变化才触发状态事件。
4. Panel Next.js Route Handler 代理内部 SSE，不向浏览器暴露 Bridge Token。
5. 页面首次加载建立 EventSource；收到快照后更新状态；连接失败时显示“正在重连”，保留手动同步。
6. 可提供 60 秒低频轮询作为兼容回退，但正常 SSE 在线时禁止同时运行 10 秒轮询。
7. Nginx 部署文档明确 `proxy_buffering off`、读取超时和连接数；内部 API 仍不映射宿主机公网端口。
8. 服务优雅关闭时结束所有流；测试不得残留线程、Task 或打开的响应体。

#### 验收标准

- 单客户端空闲一小时不再产生 360 次完整快照请求；完整快照生成和传输量下降至少 80%；
- 命令完成到面板状态更新，本机网络 p95 小于 2 秒；
- SSE 中断、Panel 重启、Bot 重启和 revision 缺口都能恢复到一份完整可信快照；
- 多个浏览器连接不会线性堆积无界内存；慢客户端不阻塞 Bot；
- SSE 只能读取状态，无法借事件端点执行命令或获取 Secret；
- 手动刷新和无 SSE 环境仍可用。

### P2-06：面板性能与诊断体验

#### 实施内容

1. 将 `OperationsRegistry` 演进为版本化、有界的运维视图，新增：
   - 最近播放：歌曲、平台、请求来源、开始/结束、结果；
   - 最近失败：结构化类型、脱敏消息、组件、时间、command_id；
   - 最近命令：类型、来源、成功/失败、耗时；
   - 外部服务：last/p50/p95、成功率、样本数；
   - 搜索缓存：容量、命中率、淘汰、并发合并。
2. CommandService 在最外层计时，后台延迟命令必须分别记录接受耗时和最终完成耗时。
3. PlaybackService 记录成功、不可播放、入语音失败、通知失败和手动停止；通知失败不能把播放成功改成失败。
4. 面板增加“实时/重连/回退轮询”连接状态，不再把“收到旧快照”显示为实时在线。
5. 队列冲突提供明确 Toast 与最新顺序，不自动提交第二次删除。
6. 指标使用易读单位和样本数；无样本、样本不足、降级和错误使用不同展示。
7. 大列表只展示固定最近数量；必要时分页，不把整个历史塞进 SSE。
8. 保持键盘操作、焦点、ARIA、颜色对比和移动端布局测试。

#### 验收标准

- 面板能解释“为什么失败”和“慢在哪里”，而不是只显示红点；
- 最近播放、失败和命令记录都有固定容量、脱敏和 schema 迁移测试；
- 状态推送不会导致页面重复 Toast、重复动画或播放进度倒跳；
- 多人队列冲突可稳定复现并得到可理解提示；
- Panel lint、build、现有渲染测试及新增 SSE/冲突/诊断测试通过。

### P2-10：Panel 待播队列拖拽排序

#### 数据与交互契约

- 写请求提交 `from_position`、`to_position` 和 `expected_version`，位置均为待播队列的一基最终位置；
- `from_position == to_position` 不写 Redis、不递增版本；越界和版本冲突必须在任何修改前失败；
- 成功响应返回新 `queue_version` 与完整 `queue_all`，Panel 不把本地乐观数组当作最终事实；
- 冲突响应使用 P2-04 的 `queue_conflict` 合约和 HTTP 409，并携带最新版本及队列；
- 当前播放不出现在可拖动容器中；重复歌曲用快照内唯一 occurrence key 区分，不依赖歌曲 ID 唯一；
- 拖动期间禁止第二次拖动和删除；收到 SSE/轮询更新时暂存，拖动结束后以写响应或最新快照收敛。

#### 实施内容

1. `QueuePort`/`QueueService` 增加单项 move 操作，定义向前、向后、首尾和同位置语义。
2. `MusicQueue` 在现有 `RLock` 中移动；Legacy Redis `QueueManager` 使用 Lua 在一次操作中校验版本、
   重排原始 JSON 字节并递增版本，不改变现有 LIST key 和元素格式。
3. 扩展现有 Panel 命令传输，使 `expected_version` 随重排写请求到达同一 QueueService；不建立第二套队列逻辑。
4. Panel 引入经过 lockfile 固定的可访问拖拽组件，使用独立手柄并支持 Pointer/Touch/Keyboard sensor。
5. 松手后乐观重排；成功采用服务端队列，普通失败恢复原顺序，409 更新到服务端最新顺序并显示明确 Toast。
6. 拖动行、落点、抓取/忙碌状态提供可辨识样式；`prefers-reduced-motion`、窄屏和触摸目标尺寸保持可用。
7. 给纯重排状态、Panel 代理、内存队列、Redis 原子脚本、Bridge 结果和双客户端冲突增加自动测试；
   鼠标、触摸、键盘与自动切歌竞态列入测试服务器验收。

#### 验收标准

- 拖动后刷新页面、Panel 重启或 Bot 重启，待播顺序仍与服务端/Redis 一致；
- 鼠标、触摸和键盘均能完成排序，删除按钮不会触发拖动，队列少于两首时手柄禁用；
- 向前、向后、首尾、重复歌曲和同位置操作结果一致，当前播放永不参与重排；
- 两个浏览器或自动出队与拖动并发时，过期请求 100% 冲突且不移动错误歌曲；
- 冲突不会自动按新编号重试，UI 在一次响应后收敛到服务端最新顺序；
- Python 测试、Panel lint/build/交互测试和 Redis 降级/恢复测试通过。

### P2-07：JM Compose Profile 与镜像拆分

#### 关键判断

Compose Profile 只能决定是否启动服务，不能从已经构建的 Bot 镜像中移除依赖。因此本任务必须同时
拆分镜像和运行时职责，不能只修改 `compose.yaml`。

#### 目标边界

- `bot`：QQ/OOPZ 音乐命令、队列、播放、Panel 状态；不安装 `jmcomic`、img2pdf、pyzipper 等
  JM 专用依赖，不复制 Node 上传器；Pillow 若仍被 QQMusic 扫码登录使用，按共享依赖保留并明确归属；
- `jm-worker`：仅在 `--profile jm` 时启动，安装 `[jm]` 和上传所需依赖，消费显式任务协议；
- Redis：保存 JM job 状态、租约和结果通知；
- 共享卷：仅交换受控任务目录/归档；路径必须限制在 `/app/data/jm-tasks`；
- Secret：jm-worker 只获得完成下载/上传所需的最小 QQ 凭据，不获得 OOPZ、QQMusic Cookie、Panel 密码。

#### 实施内容

1. 定义版本化 JM Job DTO：job_id、album_id、请求者的脱敏引用、大小/超时策略、状态、租约和结果。
2. 将下载、压缩、上传和清理从 `qqbot.py` 迁入独立 worker；Bot 只负责鉴权、提交和最终用户通知。
3. 使用 Redis Stream/List + 租约或等价机制，确保同一 job 不会并发执行两次；worker 崩溃后可恢复或超时。
4. 建立多阶段构建或独立 Dockerfile：
   - core target 安装 `.[legacy,qqmusic-login]`；
   - jm target 在 core 兼容基础上增加 `.[jm]`、Node 与上传器；
   - 默认 `docker compose build bot` 不下载 JM 包或 npm uploader 依赖。
5. `jm-worker` 设置 `profiles: ["jm"]`；标准启动不包含该服务，启用方式统一为
   `docker compose --profile jm up -d`。
6. `QQBOT_JM_ENABLED=true` 但 worker 不可达时，命令返回明确“JM 服务未启用/不可用”，音乐 Bot 继续健康。
7. installer/wizard 生成与 Profile 一致的配置；禁用 JM 不保留误导性的路径和依赖检查。
8. 分别生成 core 与 jm SBOM/依赖清单或等价测试输出，证明默认镜像不含 JM 包。

#### 验收标准

- 默认构建和运行不安装 JM 专用 Python 依赖、不运行 uploader 的 npm install、不包含 uploader；
- `--profile jm` 能完成单任务、批量任务、超限、超时、上传失败和清理；
- worker 重启不会让同一任务永久锁死或重复通知；
- 禁用/故障 JM 不影响音乐命令、QQ 网关、OOPZ、Panel 和 Bot 健康；
- Profile 开关不修改 Redis 音乐键，不删除已有 JM 任务历史；
- core 镜像体积和冷构建时间相对基线有可量化下降。

### P2-08：一键升级、备份、回滚和诊断

#### 命令建议

```text
./oopzctl diagnose [--output bundle.zip]
./oopzctl backup [--output path]
./oopzctl backup verify ARCHIVE
./oopzctl upgrade [--ref main|TAG] [--profile jm]
./oopzctl rollback [--release ID]
./oopzctl restore ARCHIVE --component data|redis|all --confirm
```

#### 实施内容

1. 复用 `scripts/backup.py` 和 `scripts/restore.py`，不复制第二套归档/校验实现。
2. 每次升级生成 release manifest：旧/新 Git SHA、镜像 ID、Profile、时间、备份路径、健康结果。
3. `upgrade` 固定流程：
   - 校验工作目录、磁盘、Docker/Compose、`.env` 权限和必填项；
   - 拒绝覆盖未提交或未知本地修改；
   - 创建并校验 data + Redis 备份；
   - fetch 明确 ref，不执行不受控脚本；
   - 构建带唯一 tag 的新镜像，不先覆盖回滚 tag；
   - 切换服务，等待 health/readyz，执行只读 smoke test；
   - 失败自动恢复旧代码引用和旧镜像，但不自动恢复数据；
   - 成功保存 manifest 并保留有限数量回滚点。
4. `rollback` 默认只切换已记录的代码/镜像；若 schema 声明不兼容则拒绝自动回滚并提示显式数据恢复。
5. `diagnose` 收集：版本、Compose Profile、容器/健康、重启次数、资源、磁盘、端口、readyz、
   QQMusic 脱敏诊断、缓存/队列版本、最近错误摘要和 Nginx 配置测试结果。
6. 诊断包默认不含 `.env`、凭证 JSON、Cookie 状态文件、Redis 原始 dump 和完整日志；所有文本再次脱敏。
7. Linux 主入口使用标准库和现有 CLI；Windows PowerShell 提供对等的检查与友好错误，不承诺在无 Docker
   环境执行生产升级。
8. 所有破坏性恢复继续要求 `--confirm`；删除旧回滚点必须单独命令且不得删除当前/最近成功版本。

#### 验收标准

- 一条命令可生成校验通过的备份和脱敏诊断包；
- 模拟构建失败、容器不健康、readyz 失败和公网 smoke test 失败时，升级不会留下半切换状态；
- 自动回滚后旧版本可读取现有 Redis 与 `data/`；
- dirty worktree、低磁盘、备份失败、未知 ref、缺 Secret 和 Profile 不匹配均在切换前停止；
- 工具不使用 `git reset --hard`、`docker compose down -v` 或宽泛删除；
- 诊断输出经过 Secret 扫描测试。

### P2-09：性能门禁、灰度和最终验收

#### 自动化矩阵

- Python：完整单元/集成测试、ruff、compileall、build；
- Panel：lint、build、SSE 重连、队列冲突、指标渲染和无障碍测试；
- Docker：core、jm 两个构建目标；默认 Compose 与 `--profile jm` 两套配置；
- Redis：旧数据读取、版本初始化、原子冲突、area 隔离、断线与恢复；
- SSE：首次快照、事件、心跳、慢客户端、断线续传、reset、关闭清理；
- 缓存：Fake Clock、LRU、并发合并、错误不缓存、容量上限；
- 运维：备份校验、升级 dry-run、失败自动回滚、诊断脱敏；
- 安全：Secret 扫描、路径穿越、命令参数注入、内部 Token 不下发浏览器。

#### 性能门槛

| 场景 | 目标 |
| --- | --- |
| Panel 空闲 1 小时 | 完整快照请求/生成量相对 10 秒轮询下降至少 80% |
| SSE 状态变化 | 本机命令完成到 UI 更新 p95 < 2 秒 |
| 相同搜索突发 | 同 key 并发只产生 1 次上游请求 |
| 搜索缓存 | 命中路径不发生网络调用；容量和 TTL 达标 |
| 队列冲突 | 过期删除 100% 返回冲突，不误删 |
| 队列拖拽 | 过期重排 100% 返回冲突；成功响应后 UI 与 Redis 顺序一致 |
| 默认镜像 | 不含 JM Python/npm 依赖，体积和冷构建时间均有记录并下降 |
| 指标存储 | 连续 24 小时后内存和状态文件保持有界 |

性能目标以同一机器、同一提交输入、同一数据量的前后对比为准；不得用公网波动证明代码回归或优化。

#### 测试服务器验收

1. 记录部署前提交、镜像、Profile、备份和资源基线；
2. 验证 Panel 首次加载、SSE 在线、断网重连、Bot/Panel 分别重启恢复；
3. 两个浏览器同时打开队列，验证一个成功删除、另一个收到冲突且不误删；
4. 使用鼠标、触摸和键盘分别重排队列，并与自动出队/另一浏览器并发，确认冲突不误移动；
5. 连续重复同一关键词，确认命中缓存并观察 TTL 后重新请求；
6. 播放可用、不可播放、Cookie 失效模拟和 QQMusic 超时，核对诊断分类；
7. 核对最近播放、失败、命令耗时和延迟没有 Secret；
8. 默认 Profile 启动音乐功能，确认系统中无 jm-worker 且 Bot 镜像不含 JM 包；
9. 启用 `jm` Profile 完成单个和批量任务，再停用 Profile，音乐功能不中断；
10. 使用统一命令创建备份、诊断包并验证归档；
11. 执行一次新镜像健康失败模拟，验证自动代码/镜像回滚；
12. 观察至少 24 小时，检查 SSE 客户端、线程、内存、状态文件、Redis key 和容器重启；
13. 保存脱敏证据，不把终端 Secret 粘贴进 Issue、PR 或执行报告。

## 8. 推荐实施批次

| 批次 | 内容 | 允许的行为变化 | 回滚单位 |
| --- | --- | --- | --- |
| A | P2-01 指标契约与容量基线 | 仅新增内部字段 | 单提交 |
| B | P2-02 搜索缓存 | 搜索延迟降低，结果语义不变 | 缓存开关/单提交 |
| C | P2-03 QQMusic 诊断 | 新增脱敏状态，不改播放判定 | 单提交 |
| D | P2-04 队列版本 | 新 Panel 写操作可能返回 409 | 单提交 + 兼容开关 |
| E | P2-05 SSE 后端与代理 | 新增事件端点，仍保留轮询 | 单提交 |
| F | P2-06 Panel 切换与诊断 UI | 正常路径停止 10 秒轮询 | 单提交 + 回退开关 |
| F2 | P2-10 队列拖拽排序 | 新增版本化重排写操作 | 单提交 + 隐藏拖动入口 |
| G1 | P2-07 JM 任务协议和 worker | 先双实现测试，不双执行 | 独立分支/镜像 |
| G2 | P2-07 默认镜像与 Profile 切换 | 默认安装不再包含 JM | 镜像与 Compose 一起回滚 |
| H | P2-08 运维 CLI | 新增宿主机命令 | 单提交 |
| I | P2-09 灰度与性能关闭 | 无新功能 | 发布回滚点 |

每批必须保持：原测试不减少、跳过项不无故增加、无真实公网写操作、无 Secret、无残留进程。
SSE 切换前先上线事件端点；JM 默认镜像瘦身前先让 worker 在 Profile 下完成真实任务。

## 9. 配置建议

建议新增配置使用安全默认值，并在 `.env.example`、配置文档和安装向导中保持一致：

```dotenv
OOPZ_SEARCH_CACHE_ENABLED=true
OOPZ_SEARCH_CACHE_TTL_SECONDS=60
OOPZ_SEARCH_CACHE_MAX_ENTRIES=256
OOPZ_SEARCH_NEGATIVE_CACHE_TTL_SECONDS=10

OOPZ_METRICS_WINDOW_SIZE=200
OOPZ_PLAYBACK_HISTORY_LIMIT=50
OOPZ_FAILURE_HISTORY_LIMIT=100
OOPZ_COMMAND_HISTORY_LIMIT=200

OOPZ_PANEL_SSE_ENABLED=true
OOPZ_PANEL_SSE_HEARTBEAT_SECONDS=20
OOPZ_PANEL_SSE_FALLBACK_POLL_SECONDS=60

QQBOT_JM_ENABLED=false
```

配置解析必须限制范围。非法值回退到文档默认并记录脱敏警告，不能让负 TTL、无限容量或 0 秒心跳
制造忙循环。Profile 是否启用不能只由 `QQBOT_JM_ENABLED` 猜测；诊断命令应同时检查 Compose 实际服务。

## 10. 风险控制与停止条件

### 高风险点

- 搜索缓存返回被调用方修改的共享字典，导致跨用户串数据；
- 401/超时被负缓存，造成恢复后仍显示“无结果”；
- 队列版本漏掉某条旧 Web/自动播放写路径，产生假一致；
- 拖动使用非唯一歌曲 ID 或在冲突后按新编号自动重试，移动了另一首歌曲；
- SSE 每次状态细节变化都生成全量快照，引发比轮询更高的 CPU/带宽；
- EventSource 重连与轮询同时运行，造成重复状态和重复提示；
- JM worker 租约错误导致任务重复上传、永久锁死或重复通知；
- 自动升级在备份未完成时切换镜像，或在健康失败后误恢复旧数据；
- 诊断包收集完整日志/环境，重新引入 Secret 泄漏。

### 立即停止条件

出现以下任一情况，停止当前批次并回到最近阶段门：

- 搜索结果、选歌会话或平台前缀行为发生未授权变化；
- 任意并发场景删除了用户未选择的歌曲；
- 任意拖动并发场景移动了用户未选择的歌曲；
- 同一命令、播放或 JM 任务被执行两次；
- SSE 断线让面板长期显示为在线，或重连后无法恢复完整状态；
- Bot/Panel 内存、线程、文件或 Redis key 持续无界增长；
- 默认镜像仍包含 JM 依赖却关闭了旧执行入口；
- 升级/回滚修改或丢失 `.env`、`data/`、Redis 卷或 Nginx 配置；
- 状态、日志或诊断包出现 Cookie、refresh key、JWT、Bridge Token、Panel 密码或完整用户标识。

### 回滚原则

- 缓存、SSE、队列乐观校验应有独立功能开关，关闭后回到已验证路径；
- 队列版本 key 为附加元数据，回滚版本可忽略，不删除；
- SSE 回滚恢复低频/原 10 秒轮询时不要求更改数据；
- JM Profile 回滚必须同时恢复上一版 Bot/JM 镜像和 Compose，不删除任务目录；
- 自动升级失败只回滚代码和镜像；数据恢复必须人工选择已校验归档；
- 禁止 `docker compose down -v`、`git reset --hard` 和宽泛递归删除作为默认回滚命令。

## 11. Definition of Done

### 代码完成条件

- P2-01 至 P2-10 均达到各自验收标准；
- 搜索缓存、指标窗口、事件队列和历史记录全部有界；
- QQMusic Cookie、延迟、可播放率和错误分类能在不泄密的前提下解释真实状态；
- Panel 正常路径使用 SSE，断线、重启和缺口可恢复，写操作仍使用独立鉴权接口；
- 所有队列编号变更都受版本保护，过期 Panel 操作不会误删；
- Panel 拖拽重排持久化到真实队列，鼠标、触摸、键盘和冲突恢复均通过验收；
- 最近播放、失败原因、命令耗时和外部服务延迟可用且容量受控；
- 默认 Bot 镜像实际不包含 JM 依赖，`jm` Profile 可独立启停；
- 一键升级、备份、诊断和代码/镜像回滚均有自动化失败测试；
- 完整 Python、Panel、Uploader、core/jm Docker 与 Compose Profile 测试通过；
- 文档、配置样例、安装器、运维命令和实际行为一致。

### 生产关闭条件

代码完成不等于生产关闭。只有额外满足以下条件，P2 才可标记完成：

- 同机前后性能基线达到本计划门槛，没有用缓存/SSE 换来错误状态；
- 测试服务器完成 SSE、双客户端队列冲突、QQMusic 故障和 JM Profile 验收；
- 一键升级失败模拟与旧镜像回滚成功，旧版本仍能读取现有 Redis 和 `data/`；
- 24 小时观察内无容器重启、重复处理、状态漂移、无界增长或 Secret 泄漏；
- 默认无 JM 与启用 JM 两种生产组合均有明确、可重复的部署记录；
- 用户或运维确认可以替换旧的手工升级步骤。

## 12. Agent 最终交付格式

执行完成后不得只报告“性能优化完成”。最终报告至少包含：

1. 完成任务：P2-01...P2-10；
2. 提交/分支/镜像：SHA、镜像 ID、Profile；
3. 外部行为变化：SSE、队列 409、JM 启用方式；
4. 数据变化：新增 Redis key、状态 schema 和回滚兼容性；
5. 搜索缓存：TTL、容量、命中率、上游请求减少量；
6. QQMusic：样本窗口、延迟、可播放率和错误分类结果；
7. Panel：轮询前后请求量、更新延迟、断线恢复结果；
8. 队列并发：双客户端冲突证据和误删测试；
9. 队列拖拽：鼠标/触摸/键盘、持久化、自动出队竞态和重复歌曲测试；
10. JM：默认/core 与 jm 镜像依赖、大小、构建时间和任务验收；
11. 运维工具：备份、诊断、升级失败和回滚证据；
12. 自动化测试：Python、Panel、Uploader、Docker、Profile；
13. 生产验收：开始/结束时间、资源、重启、错误和 Secret 扫描；
14. 未完成项和明确风险；
15. 当前回滚点与不删除数据的回滚命令。
