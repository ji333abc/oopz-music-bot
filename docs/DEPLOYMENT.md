# 部署指南

现有 `/home/oopzbot/Oopzbot` 旧版服务器需要迁移时，请使用完整的 [旧版迁移到 Docker 指南](MIGRATION_FROM_LEGACY.md)，不要直接覆盖旧目录或同时启动两套 QQ Bot。

## Docker Compose

```bash
python scripts/init_config.py
# 编辑 .env
docker compose up -d --build
docker compose logs -f bot
```

发布、升级、回滚和诊断统一使用仓库根目录入口：

```bash
./oopzctl diagnose --output diagnose.zip
./oopzctl dependencies --output dependency-manifest.json
./oopzctl backup --output backups/pre-deploy.zip
./oopzctl backup verify backups/pre-deploy.zip
./oopzctl upgrade --ref main --dry-run
./oopzctl releases prune --keep 5
```

备份默认不包含 `.env`。需要恢复时先停止 Bot，使用已校验的归档并显式确认：

```bash
./oopzctl restore backups/<verified-backup>.zip --component all --confirm
docker compose up -d
```

恢复会先自动备份当前数据，不使用删除数据卷的命令。

完成 dry-run 和审查后更新：

```bash
./oopzctl upgrade --ref main
```

升级会拒绝 dirty worktree、过宽的 `.env` 权限和 JM 开关/Profile 不一致，先创建并校验 data + Redis 备份；每个提交使用唯一 Bot/Panel/QQMusic/JM 镜像标签。readyz、Panel 容器健康和配置的公网 `/api/health` 任一失败时，只恢复 manifest 记录的旧代码/旧镜像，不自动覆盖数据。回滚使用 `./oopzctl rollback --release <ID>`。旧回滚点只由显式 `releases prune` 删除，且至少保留最近两个成功版本。

停止：

```bash
docker compose down
```

默认 Compose 启动 Bot、Redis、固定版本 QQ 音乐 API 和 Web 管理面板。音乐接口与机器人命令桥接只在 Compose 内部网络开放；面板默认映射到宿主机 `127.0.0.1:3000`。运行数据挂载到仓库的 `data/`。JM 使用独立镜像和 Profile：

```bash
docker compose --profile jm up -d --build
docker compose --profile jm logs -f jm-worker
```

面板通过 SSE 接收语义状态变化，断线时自动切换轮询；支持完整待播队列、鼠标/触摸/键盘拖拽排序、乐观版本冲突恢复、播放控制、诊断指标、结构化频道成员和 JM 历史。浏览器不会获得 `QQBOT_BRIDGE_TOKEN`，写操作仍由面板服务端 POST 转发。历史记录有固定容量，不包含解压密码、Cookie 或播放 URL。

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

Nginx 代理事件流时必须关闭缓冲并给长连接足够的读取超时；普通页面/API 可继续使用既有规则：

```nginx
location /api/events {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 1h;
    proxy_send_timeout 1h;
}
```

默认 Bot 镜像只包含音乐运行时、Chromium 和 QQ Bot，不包含 JM Python/npm 依赖或上传器。`jm-worker` Profile 才包含这些组件。长任务结果仍由 Bot 发送；原消息过期或回复次数耗尽时会自动尝试群主动消息。

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
