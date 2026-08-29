# OOPZ Music Bot 架构

## 运行关系

```text
QQ SDK ─┐
        ├─> 18080 内部桥接 ─> CommandService
Panel ──┘                         │
                                 ├─> PlaybackService ─> Legacy OOPZ Runtime Adapter
                                 ├─> QueueService ─> QueuePort Adapter ─> Redis/内存降级
                                 └─> QQMusic Provider

QQ SDK ─> ReplyPolicy
       └> JM task coordinator ─> downloader/uploader/retention adapters
```

容器仍为 `redis`、`qqmusic`、`bot`、`panel`。Redis、QQMusic 和 18080 仅在 Compose
网络中使用；Panel 与旧 Web 默认只绑定宿主机 `127.0.0.1`。

## 模块所有权

| 层 | 模块 | 所有权 |
| --- | --- | --- |
| 领域 | `oopzbot/domain` | DTO、错误枚举、Port；不导入框架或基础设施 |
| 应用 | `oopzbot/application` | 命令、播放结果和队列业务语义 |
| 命令 | `oopzbot/commands` | 纯解析、别名注册表 |
| HTTP | `oopzbot/http` | 内网和 Token 鉴权、输入校验 |
| QQ | `oopzbot/qq` | QQ 文案清理、有限重试与主动发送降级 |
| JM | `oopzbot/jm` | 子进程、上传、任务锁、清理策略 |
| 基础设施 | `oopzbot/infrastructure` | 旧队列到 QueuePort 的兼容转换 |
| OOPZ | `oopzbot/legacy_runtime.py` | 凭据、WebSocket、Agora 和旧实现适配 |

## 关键不变量

- QQ 与 Panel 的控制命令都进入 18080 的同一 `CommandService`。
- 待播队列编号始终从 1 开始，当前歌曲不属于待播编号。
- 批量删除先完整校验；Redis 使用单条 Lua 操作，内存队列使用同一临界区。
- Redis 键和旧扁平 JSON 保持不变。
- Redis 降级队列或播放状态非空时不自动切回，防止静默丢队列；清空后才能切回真实 Redis。
- QQ 被动回复最多重试一次，随后最多主动发送一次；无主动权限立即结束。
- JM 子进程只获得允许列表中的环境变量。
- 当前 OOPZ 实现通过日志和 `/readyz`、Panel 快照的 `runtime_implementation` 明示。

## 仍保留的兼容边界

旧核心仍提供 OOPZ 收包、非音乐管理命令、消息发送、旧 Web、Redis 播放状态和 Agora
推流。它通过 `LegacyOopzRuntimeAdapter` 启动，并已作为 wheel 的声明包内容，不再只依赖
Docker `COPY` 才能存在。

旧 Web 与旧 OOPZ 命令尚未删除。它们存在真实运行时调用方，且删除门禁要求测试服务器、
回滚演练和 24 小时观察证据；本地重构不能伪造这些证据。

## 并发与进程模型

- QQ SDK 管理异步事件循环，慢命令在线程中调用 18080，不发送中间“正在处理”回复。
- FastAPI 在后台线程监听；命令临界区继续使用既有 `_command_lock`，本轮未改变锁模型。
- OOPZ WebSocket 使用旧核心已验证的分片工作队列、认证、心跳和重连实现。
- Agora 播放与完成回调仍在旧运行时内；应用层通过 PlaybackService/Runtime Port 接触能力。
- JM 下载与上传使用子进程，取消或超时会终止上传子进程并由 Service 释放任务锁。
- Redis 批量删除在服务端 Lua 脚本内完成，避免跨线程或跨客户端的部分删除。

## 数据与持久化

Compose 使用启用 AOF 的 Redis 保存播放队列。键名继续为 `music:queue`、
`music:current`、`music:default_channel`、`music:play_state` 和 `music:play_mode`；带域时沿用
原域隔离规则。`data/legacy/` 保存旧核心数据库、插件状态和登录凭据刷新结果，`data/` 还保存
JM 状态。禁止使用 `docker compose down -v` 进行更新或回滚。

## 安全边界

- 18080 校验来源地址和随机 `QQBOT_BRIDGE_TOKEN`；Redis 与 QQMusic 不暴露宿主端口。
- QQ 群与 JM 均可独立设置 OpenID 白名单。
- Secret 只从环境或权限受限的持久目录读取，不进入状态响应和普通日志。
- QQMusic、JM worker 和上传器子进程使用环境变量允许列表，不继承整个 Bot 环境。
- Panel 密码和 RackNerd 凭据只注入 Panel 容器，Bot Compose 环境显式清空这些值。
- 旧 Web 与 Panel 默认仅绑定 `127.0.0.1`，公网访问应由带 TLS 和认证的 Nginx 代理提供。
