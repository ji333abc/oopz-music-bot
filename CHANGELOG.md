# Changelog

本项目遵循 Keep a Changelog 风格。当前发布版本由 `oopzbot.__version__` 提供，Panel
`package.json` 和 Git Tag 必须在发布前与它保持一致。

## [Unreleased]

### Added

- 专辑点歌模式产品与技术规划书，以及可灰度启用的 QQ 群、OOPZ 文字频道和 Panel 操作闭环：支持专辑检索、曲目选择、整张/前 N 首/区间原子入队，并在歌曲开播时解析最新地址、跳过失效曲目。
- QQ 音乐扫码登录凭证存储、Cookie 状态文件、动态读取与后台自适应刷新。
- `oopzbot qqmusic-login` 的登录、状态、刷新和 Cookie 查询命令。
- QQ Music API 内部 Cookie 热更新端点，以及 Compose/本地托管的分发与重启兜底。
- Panel 和健康快照中的 QQ 音乐凭证状态（不含任何密钥）。
- Panel SSE 实时状态通道，包含 snapshot、state、reset 和 heartbeat 事件，并保留低频完整快照校准。
- 搜歌结果 TTL/LRU 缓存、同请求合并、负缓存和可配置容量上限；播放 URL 与认证失败不会进入缓存。
- QQMusic 外部请求的 last/p50/p95、成功率、错误分类、可播放率，以及命令、播放和失败历史诊断。
- 待播队列的鼠标、触摸和键盘拖拽排序，以及基于 `queue_version` 的乐观并发控制。
- 独立 `jm-worker` 镜像和 Compose `jm` Profile，通过 Redis 队列、租约续期和结果栅栏隔离长任务。
- `oopzctl` 诊断、依赖清单、备份校验、安全升级、自动回滚和回滚点清理入口。

### Changed

- Panel 状态同步由高频完整轮询改为 SSE 语义通知加默认 60 秒校准，显著减少空闲快照请求。
- Panel 状态文件升级到 schema 2；所有历史、指标和状态均有固定容量及 512 KiB 文件上限。
- Bot 默认 `core` 镜像不再包含 JM Python/npm 依赖、Node 上传器或任务执行路径。
- 旧 Web 播放器和管理后台的队列写入统一经过版本化 `QueueManager`。

### Fixed

- 安全升级和自动回滚现在会等待 Bot 与 Panel 完成启动并重试健康检查，不再因容器启动瞬间的连接拒绝误判失败。
- 直接运行仓库的 `./oopzctl` 时会自动加载项目包，不再要求运维人员手动设置 `PYTHONPATH`。
- 队列内容与 `queue_version` 现在通过共享锁或 Redis Lua 原子读取，避免并发自动切歌时删除或移动错误歌曲。
- 拖拽排序请求发生网络或响应解析错误时会撤销乐观顺序并重新同步服务端状态。
- SSE 在线时仍执行低频校准，自然切歌、旧 Web 写入和 worker 心跳变化不会使面板长期停留在旧状态。
- SSE 等待改为异步通知，不再由每个连接长期占用命令和就绪探测共享的默认线程池。
- Linux 仓库入口 `oopzctl` 现在带可执行权限，可直接按部署文档使用 `./oopzctl`。
- JM worker 的 Redis 读取超时现在长于阻塞取任务周期，空队列时不会因超时竞争反复退出重启。

### Security

- 新增的状态、诊断和历史数据在写盘及导出前统一限制容量并脱敏 URL、Cookie、Token 和用户标识。
- JM worker 只接收 QQ App、Redis 和任务限制配置，不接收 OOPZ、QQMusic Cookie 或 Panel 密钥。

### Upgrade notes

- 专辑点歌默认关闭；灰度启用时设置 `OOPZ_ALBUM_REQUEST_ENABLED=true`。可用 `OOPZ_ALBUM_REQUEST_MAX_TRACKS` 和 `OOPZ_ALBUM_REQUEST_SESSION_TTL_SECONDS` 调整单次入队上限与会话有效期；回滚只需将开关改回 `false` 并重启服务，不需要数据迁移。
- 当前候选分支：`codex/p2-performance-panel`；部署时以远程分支最新提交为准。
- 升级前确保服务器工作树干净、`.env` 已配置 `QQBOT_BRIDGE_TOKEN` 和 `OOPZ_PANEL_PASSWORD`，并至少保留 1 GiB 可用磁盘。
- 默认部署先执行 `./oopzctl upgrade --ref codex/p2-performance-panel --dry-run`，确认后执行同一命令并移除 `--dry-run`。
- 启用 JM 时必须设置 `QQBOT_JM_ENABLED=true`，并在两条升级命令中同时添加 `--profile jm`。
- 升级工具会在切换前创建并校验 data/Redis 备份；健康检查失败时恢复旧提交和旧镜像，不自动覆盖数据。
- 回滚使用 `./oopzctl rollback --release <RELEASE_ID>`；只有明确需要恢复数据时才运行带 `--confirm` 的 restore。

## [0.1.1] - 2026-08-28

### Fixed

- 恢复目标只能指向 Compose 项目的 `data/`，数据目录使用原子替换且归档不能夹带 `.env`。
- 活跃 SQLite 数据库通过在线备份 API 创建一致快照，备份归档从创建开始即使用受限权限。
- Redis、QQMusic 故障不再阻止 Bot 和 Panel 启动；Redis 运行中断开会降级并自动恢复。
- OOPZ WebSocket 和 Redis 组件状态改为真实连接探测，避免重连线程造成假在线。
- 面板事件和 JM 错误在持久化前统一脱敏。

### Security

- 恢复工具拒绝宽泛目录、Windows 路径穿越、符号链接和隐藏的 `.env` 载荷。
- 扩大日志与状态脱敏字段范围，覆盖旧版管理密码、Redis 密码和 OneBot 凭据。

## [0.1.0] - 2026-08-28

### Added

- P0 稳定性基线 CI：Python 3.11/3.12、Panel、QQ 上传器和 Docker/Compose 验证。
- Bot `/healthz` 存活接口、`/readyz` 就绪接口和统一组件快照。
- QQ 与 Panel 命令的 `command_id` 关联日志，以及日志 Secret 脱敏。
- 本地 Fake/Stub 外部依赖故障测试。
- `scripts/backup.py` 和 `scripts/restore.py` 数据、Redis 快照备份恢复工具。

### Changed

- Panel 现在消费 Bot 统一组件快照，并显示有限状态：`starting`、`ok`、`degraded`、`error`、`offline`、`unknown`。
- QQMusic 的 HTTP 404、超时和网络故障返回具体的音乐错误。

### Fixed

- Redis 内存降级、QQMusic 空播放地址、JM 下载失败等路径的状态和错误可定位。
- 恢复流程会在写入前验证校验清单，并自动保存当前数据。

### Security

- 日志不输出 AppSecret、Bridge Token、Cookie、JWT、密码、私钥和 RackNerd 凭据。
- 备份默认不包含 `.env`，恢复必须显式提供 `--confirm`。
- Redis、QQMusic 和 Bot 桥接端口继续只在 Compose 私有网络中开放。

### Migration notes

- 本阶段不修改 `.env` 字段；现有配置保持兼容。
- 升级后可用 `/healthz`、`/readyz` 和 Panel 组件状态检查运行状况。
- 详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)、[docs/MIGRATION_FROM_LEGACY.md](docs/MIGRATION_FROM_LEGACY.md) 和 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)。
