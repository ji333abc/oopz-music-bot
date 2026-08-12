# 部署指南

## Docker Compose

```bash
python scripts/init_config.py
# 编辑 .env
docker compose up -d --build
docker compose logs -f bot
```

更新：

```bash
git pull
docker compose up -d --build
```

停止：

```bash
docker compose down
```

容器只需要出站网络，不需要映射端口。运行数据挂载到仓库的 `data/`。

## systemd

以下路径与 `deploy/oopzbot.service` 一致：

```bash
id -u oopzbot >/dev/null 2>&1 || sudo useradd --system --home-dir /opt/oopz-music-bot --shell /usr/sbin/nologin oopzbot
sudo install -d -o oopzbot -g oopzbot /opt/oopz-music-bot
sudo cp -a . /opt/oopz-music-bot/
sudo chown -R oopzbot:oopzbot /opt/oopz-music-bot
cd /opt/oopz-music-bot
sudo -u oopzbot sh scripts/bootstrap.sh
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

Playwright 浏览器会安装到 `/opt/oopz-music-bot/.playwright`，服务可读取但不能修改。服务使用 `ProtectSystem=full`，只允许写入 `/opt/oopz-music-bot/data`。

## 故障排查

- 启动前先运行 `oopzbot check`。
- 找不到频道 ID 时运行 `oopzbot discover`。
- 语音初始化失败时确认 Chromium 已安装；容器内路径为 `/usr/bin/chromium`。
- 搜索正常但无法播放时检查音乐 Cookie、音质和音乐 API 日志。
- QQ 群无响应时确认机器人已启用群消息事件，并检查群白名单。
