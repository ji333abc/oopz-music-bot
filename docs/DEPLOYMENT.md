# 部署指南

现有 `/home/oopzbot/Oopzbot` 旧版服务器需要迁移时，请使用完整的 [旧版迁移到 Docker 指南](MIGRATION_FROM_LEGACY.md)，不要直接覆盖旧目录或同时启动两套 QQ Bot。

## Docker Compose

```bash
python scripts/init_config.py
# 编辑 .env
docker compose up -d --build
docker compose logs -f bot
```

发布或升级前先保存数据和 Redis 快照：

```bash
python scripts/backup.py --data-dir data --output backups/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ).zip
docker compose config --quiet
```

备份默认不包含 `.env`。需要恢复时先停止 Bot，使用已校验的归档并显式确认：

```bash
python scripts/restore.py backups/<verified-backup>.zip --data-dir data --component all --confirm
docker compose up -d
```

恢复会先自动备份当前数据，不使用删除数据卷的命令。

更新：

```bash
git pull
docker compose up -d --build
```

停止：

```bash
docker compose down
```

Compose 同时启动 Bot、固定版本 QQ 音乐 API 和 Web 管理面板。音乐接口与机器人命令桥接只在 Compose 内部网络开放；面板默认映射到宿主机 `127.0.0.1:3000`。运行数据挂载到仓库的 `data/`。

面板支持实时播放状态、完整待播队列、搜歌前 10 首、播放控制、删除指定队列歌曲、结构化频道成员、组件健康、真实事件及 JM 任务历史。浏览器不会获得 `QQBOT_BRIDGE_TOKEN`，所有控制请求由面板服务端转发。JM 和事件记录保存在 `data/panel-state.json`，不包含解压密码。

本机查看：

```text
http://127.0.0.1:3000
```

Bot 的存活和就绪检查分别为 `http://127.0.0.1:18080/healthz` 与
`http://127.0.0.1:18080/readyz`。`healthz` 不访问外部服务；`readyz` 会在有限超时内返回
Redis、QQMusic、OOPZ WebSocket、OOPZ 语音、QQ Bot 和旧核心组件状态。面板的状态接口
直接消费同一份组件快照。

首次启动前必须在 `.env` 设置高强度的 `OOPZ_PANEL_PASSWORD`；默认用户名是 `admin`。面板自身启用 HTTP Basic Auth。若使用域名，推荐让现有 Nginx/Caddy 反向代理到这个地址并启用 HTTPS。设置正确的公开地址可用于页面分享信息：

```dotenv
OOPZ_PANEL_PUBLIC_URL=https://panel.example.com
```

RackNerd 流量卡片为可选功能；在 `.env` 填写 `RACKNERD_API_KEY` 和 `RACKNERD_API_HASH` 后生效。

Bot 镜像已包含 OOPZ 运行时、Chromium、QQ/JM 机器人及 Node.js 文件上传器；QQ Music API 使用独立容器。长任务优先使用原消息进行被动回复，原消息过期或回复次数耗尽时会自动尝试群主动消息。群主仍需在 QQ 的机器人设置中允许主动消息，否则平台会拒绝该兜底发送。

从旧版 `main.py + qqbot_service.py` 部署迁移时，必须先停止两个旧进程再启动 Compose，避免同一 AppID 重复接收消息。将旧环境变量合并到根目录 `.env`，并把需要保留的数据复制到 `./data/` 后执行：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

查看全部服务状态或面板日志：

```bash
docker compose ps
docker compose logs -f panel
```

## systemd

以下路径与 `deploy/oopzbot.service` 一致：

```bash
id -u oopzbot >/dev/null 2>&1 || sudo useradd --system --home-dir /opt/oopz-music-bot --shell /usr/sbin/nologin oopzbot
sudo install -d -o oopzbot -g oopzbot /opt/oopz-music-bot
sudo cp -a . /opt/oopz-music-bot/
sudo chown -R oopzbot:oopzbot /opt/oopz-music-bot
cd /opt/oopz-music-bot
sudo -u oopzbot sh install.sh
sudo chown -R root:root /opt/oopz-music-bot
sudo install -d -o oopzbot -g oopzbot /opt/oopz-music-bot/data
sudo install -o root -g oopzbot -m 0640 .env /etc/oopzbot.env
sudo chown root:oopzbot .env
sudo chmod 0640 .env
sudo install -o root -g root -m 0644 deploy/oopzbot.service /etc/systemd/system/oopzbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now oopzbot
```

检查状态：

```bash
systemctl status oopzbot
journalctl -u oopzbot -f
```

安装脚本会把固定版本音乐 API 放到 `/opt/oopz-music-bot/.services/qqmusic-api`。Playwright 浏览器安装在 `/opt/oopz-music-bot/.playwright`；服务只需读取这两个目录。服务使用 `ProtectSystem=full`，只允许写入 `/opt/oopz-music-bot/data`。

## 故障排查

- 启动前先运行 `oopzbot check`。
- 找不到频道 ID 时运行 `oopzbot discover`。
- 语音初始化失败时确认 Chromium 已安装；容器内路径为 `/usr/bin/chromium`。
- 搜索正常但无法播放时检查音乐 Cookie、音质，以及 `journalctl -u oopzbot` 中的音乐 API 日志。
- QQ 群无响应时确认机器人已启用群消息事件，并检查群白名单。
