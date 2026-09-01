# P2 性能与面板体验执行报告

> 代码状态：完成，等待测试服务器生产关闭审计
> 分支：`codex/p2-performance-panel`
> 基线 SHA：`efac2c9a162a646d5a89c411d49ac0308b8c26cc`
> 本地环境：Python 3.12.6、Node 24.19.0、无 Docker
> 执行日期：2026-08-31（Asia/Shanghai）

## 1. P2-01 至 P2-10 完成情况

| ID | 结果 | 主要证据 |
| --- | --- | --- |
| P2-01 | 完成 | 独立指标 DTO、单调计时、有界窗口、状态 schema 2、512 KiB 上限与 URL/Secret 脱敏 |
| P2-02 | 完成 | 60 秒 TTL、10 秒负缓存、256 LRU、同 key singleflight、深拷贝、错误旁路与开关 |
| P2-03 | 完成 | QQMusic 端点 last/p50/p95、成功率、结果分类、可播放率与凭证刷新摘要 |
| P2-04 | 完成 | area 隔离的单调 `queue_version`、内存/Redis 原子校验、删除/清空/移动 409 合约 |
| P2-05 | 完成 | Bot SSE、Panel 同源代理、snapshot/state/reset/heartbeat、缺口恢复、60 秒回退轮询与关闭唤醒 |
| P2-06 | 完成 | 最近播放、失败、命令耗时、外部延迟、缓存和凭证诊断 UI；全部历史有界 |
| P2-07 | 完成 | core/jm-worker 多阶段目标、Compose `jm` Profile、版本化 Job、租约续期/栅栏、崩溃恢复与清理 |
| P2-08 | 完成 | `oopzctl` 诊断、依赖清单、备份校验、唯一镜像升级、失败回滚、显式恢复与回滚点清理 |
| P2-09 | 本地门禁完成 | Python、Panel、Uploader、lint、compileall、package build、Compose 静态解析与性能门禁通过 |
| P2-10 | 完成 | lockfile 固定 `@dnd-kit`，Pointer/Touch/Keyboard、独立手柄、乐观重排、版本冲突收敛 |

## 2. 外部行为与数据变化

- Panel 正常连接只消费 SSE；首次连接取完整 snapshot，语义变化触发校准，断线才启用 60 秒轮询。
- Panel 删除、清空和移动携带 `expected_version`；过期写返回 HTTP 409、最新 `queue_version`、`queue` 和兼容字段 `queue_all`，客户端不自动重试旧位置。
- 拖拽只作用于待播队列。鼠标、触摸和键盘共享同一个服务端 `移动 <from> <to>` 写路径；重复歌曲使用 `revision:index:id/name` 快照实例键。
- 新增 Redis 元数据 `music:queue_version` / area 版本 key；旧版本可忽略，不要求清空原 LIST。
- JM 新增 `oopzbot:jm:jobs:v1`、`oopzbot:jm:processing:v1`、心跳、租约和结果 key。租约使用随机 token、持续续期和完成栅栏，旧 worker 结果不能覆盖新租约。
- `panel-state.json` 升到 schema 2；旧组件、事件和 JM 历史迁移保留，新增播放/命令/失败/外部指标记录。

## 3. 缓存、QQMusic 与 Panel 性能

- 搜索缓存默认 TTL 60 秒、负缓存 10 秒、容量 256；配置范围分别为 5–600、1–60、16–2048。
- 相同 key 并发测试只产生一次 loader 调用；命中返回深拷贝。QQMusic 的认证、HTTP、超时、网络和解析失败即使表现为空列表也不会进入负缓存。
- 指标默认每端点 200 个样本、最多 64 个序列；报告 last/p50/p95、成功/失败、成功率和结果类别。可播放率按一次最终取址计一个样本。
- 旧 10 秒轮询为 360 次完整快照/小时。即使按最保守的 60 秒断线回退计算也只有 61 次（含首次），减少 83.1%；SSE 正常空闲时只有首次完整 snapshot。
- 本地 revision 发布到等待者的门禁小于 2 秒；发布器使用 50 ms 合并窗口，慢客户端只读取最新 revision，不保存每客户端无界队列。

真实 QQMusic 延迟、缓存命中率和播放率必须使用测试服务器自身窗口采集，本报告不以公网模拟值冒充生产数据。

## 4. 队列并发与拖拽证据

- 内存和 Legacy Redis 适配器覆盖向前、向后、首尾、同位置、重复歌曲、越界、删除、清空和 area 隔离。
- 自动出队、随机出队以及“缺少语音频道后回退到队首”均会推进版本；旧 Web 播放器和管理后台的新增、置顶、删除、清空也已统一进入 `QueueManager`，不再存在直接 LIST 写入绕过路径。
- 自动门禁连续提交 100 次过期移动，100 次均冲突，队列顺序未改变。
- Panel 在拖动期间阻止删除/第二次写；成功采用服务端完整队列，普通失败恢复原顺序，409 采用最新服务端顺序。
- 触摸使用延迟/容差激活，键盘使用 sortable keyboard coordinates；手柄有 ARIA 标签和快捷键，少于两首时禁用，并遵循 reduced-motion CSS。

## 5. JM 镜像与任务边界

- `core` 安装 `.[legacy,qqmusic-login]`，不安装 `jmcomic`、`img2pdf`、`pyzipper`，不复制 Node uploader；Pillow 由扫码登录共享需求保留。
- `jm-worker` 才安装 `.[jm]`、Redis client、Node/npm 和 uploader，并只接收 QQ App、Redis、大小/超时配置，不接收 OOPZ、QQMusic Cookie 或 Panel Secret；Bot 已删除本地下载/上传执行模式和本地 uploader 健康检查。
- 默认 Compose 不启动 `jm-worker`；启用命令为 `docker compose --profile jm up -d --build`，且 `.env` 的 `QQBOT_JM_ENABLED` 必须与 Profile 一致。
- 自动测试覆盖原子批量 FIFO、单任务成功、超限、下载超时、上传失败、目录清理、租约恢复/续期和过期 worker 完成栅栏；架构门禁禁止 Bot 重新引入本地 JM 执行路径。
- `oopzctl dependencies` 已证明 core 无 JM Python 包和 Node uploader。镜像字节大小与冷构建时间因本机无 Docker，留给测试服务器实测。

## 6. 运维工具与回滚

- `oopzctl diagnose` 只收集选定的版本、Profile、健康、资源、队列/缓存/QQMusic 脱敏摘要；排除 `.env`、凭证、Cookie 文件、Redis dump 和原始日志。
- `upgrade` 在切换前检查 dirty worktree、磁盘、Docker/Compose、`.env` 权限/必填项、JM Profile；创建并验证 data + Redis 备份，再 fetch 明确 ref。
- 新镜像按新 SHA 使用唯一标签。readyz、Panel 容器健康或公网 `/api/health` 失败时，恢复 manifest 中的旧 SHA 和旧镜像标签，不恢复数据，并保存失败 manifest。
- `rollback` 只接受记录中的 40 位 SHA、已知 Profile 和已记录镜像；schema 不兼容时拒绝自动回滚。
- 数据恢复单独执行并强制 `--confirm`。清理旧回滚点使用 `oopzctl releases prune --keep N`，`N` 不得小于 2。

## 7. 自动化结果

| 门禁 | 结果 |
| --- | --- |
| Python | 253 passed，2 个既有环境条件 skip |
| P2 作用域 Ruff | passed（`oopzbot`、`tests`、`scripts/oopzctl.py` 及触及的 Legacy 音乐、队列与 Web 入口） |
| compileall | passed |
| Python sdist/wheel | passed（`python -m build --no-isolation`） |
| Panel lint | passed |
| Panel build/test | passed，4/4（Node 24.19.0） |
| Uploader | passed，6/6（Node 协议 3 项 + Python worker 适配器 3 项） |
| Compose | PyYAML 静态解析 passed；Docker CLI 不存在，未执行 `docker compose config/build` |
| 性能门禁 | 60 秒回退减少 83.1%、本地 revision <2 秒、86,400 样本后窗口仍为 200、100/100 过期移动冲突 |

全仓 Ruff 仍会报告旧核心及独立 `jm-qqbot` 的 386 条既有格式债务；本次没有用批量自动修复扩散到无关代码。P2 作用域为零错误。

## 8. 测试服务器生产关闭清单

以下属于计划明确区分的“生产关闭”，是审计输入，不是本地代码结果：

1. 记录部署前 SHA、镜像 ID、Profile、备份和资源基线；
2. 实构 core/jm 两个目标和默认/jm 两套 Compose，记录镜像大小与冷构建时间；
3. 验证 SSE 首次加载、断网重连、Bot/Panel 分别重启及 revision 缺口 reset；
4. 用两个真实浏览器验证删除/清空/拖拽冲突；鼠标、触摸、键盘分别验收；
5. 与自动出队并发拖动，确认 409 后没有误移动；
6. 采集真实缓存命中、TTL 失效、QQMusic p50/p95、错误分类与可播放率；
7. 默认 Profile 验证无 jm-worker、core 内无 JM 包；jm Profile 验证单个和批量真实任务；
8. 执行真实备份、诊断 Secret 扫描、健康失败升级和旧镜像回滚；
9. 连续观察 24 小时的内存、线程、状态文件、Redis key、容器重启、重复处理和 Secret 泄漏。

审计完成前，P2 只能标记“代码完成”，不能标记“生产关闭”。

## 9. 当前回滚信息

- 当前工作树尚未提交，未生成 release ID 或镜像 ID；没有执行生产切换。
- 代码审计通过后应按计划把 P2-04、P2-05、P2-07 分成独立提交/回滚单位，不要压成一个不可拆回滚提交。
- 已部署版本回滚：`./oopzctl rollback --release <ID>`。
- 仅在明确选择数据归档时恢复：`./oopzctl restore <ARCHIVE> --component data|redis|all --confirm`。
- 禁止使用 `git reset --hard` 或 `docker compose down -v` 作为回滚步骤。
