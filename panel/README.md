# OOPZ Control

Oopzbot 的 Web 管理面板，用于查看播放状态和队列、搜索与控制音乐、查看频道信息及可选的服务器资源指标。

面板基于 React 和 Vinext，以 Node.js standalone 方式运行，并由仓库根目录的 Docker Compose 统一部署。

它通过服务端代理访问 Oopzbot：浏览器不会看到桥接 Token，机器人桥接端口也无需映射到宿主机。

## 本地开发

要求 Node.js 22.13 或更高版本。

```bash
npm ci
copy .env.example .env.local   # Windows
# cp .env.example .env.local   # macOS / Linux
npm run dev
```

无法连接后端时，页面会明确显示数据不可用，不再保留或生成演示状态。

## 环境变量

| 变量 | 用途 |
| --- | --- |
| `QQBOT_BRIDGE_TOKEN` | 服务端调用 Oopzbot 命令桥接时的共享密钥 |
| `OOPZBOT_BRIDGE_URL` | Oopzbot 命令桥接地址；Compose 中为 `http://bot:18080/internal/qqbot/command` |
| `OOPZBOT_PANEL_SNAPSHOT_URL` | 播放、频道、健康、事件和 JM 状态的结构化快照地址 |
| `OOPZ_PANEL_PUBLIC_URL` | 面板公开地址，用于页面分享元数据 |
| `OOPZ_PANEL_USERNAME` | 面板 HTTP Basic Auth 用户名 |
| `OOPZ_PANEL_PASSWORD` | 面板 HTTP Basic Auth 密码；Docker Compose 中必填 |
| `RACKNERD_API_KEY` | 可选的 RackNerd API Key |
| `RACKNERD_API_HASH` | 可选的 RackNerd API Hash |

所有变量都只在服务端使用，不应添加 `NEXT_PUBLIC_` 前缀。不要提交 `.env.local`。

## 常用命令

```bash
npm run dev
npm run build
npm test
npm run lint
```

## Docker 部署

在仓库根目录运行：

```bash
docker compose up -d --build
docker compose logs -f panel
```

默认仅监听 `127.0.0.1:3000`，并由面板自身执行 HTTP Basic Auth。需要域名访问时，把 Nginx/Caddy 反向代理到该地址并启用 HTTPS。

不要关闭访问认证后把管理面板开放到公网，也不要提交填有真实凭据的环境文件。
