# 从旧版 systemd 部署迁移到 Docker Compose

本文适用于当前 RackNerd 服务器上的旧部署：

- OOPZ 主程序：`/home/oopzbot/Oopzbot/main.py`，由 `oopzbot.service` 管理；
- QQ Bot：`/home/oopzbot/Oopzbot/qqbot_service.py`，使用独立 Python 虚拟环境；
- QQMusic API：`/home/oopzbot/qqmusic-api/server.mjs`，使用独立 Node.js 进程；
- JM 临时目录：`/home/oopzbot/jm-tasks`。

迁移后的 Docker Compose 会统一运行三个服务：

- `bot`：OOPZ 音乐机器人、QQ Bot、JM 下载与 QQ 文件上传；
- `qqmusic`：项目固定版本的 QQMusic API；
- `panel`：带登录保护的 Web 管理面板。

建议把新版本安装到 `/opt/oopz-music-bot`，不要覆盖旧目录。这样出现问题时可以直接停容器并恢复旧服务。

## 迁移前必须知道的事项

1. 同一个 QQ AppID 不能同时运行旧 QQ Bot 和新容器，否则会重复接收消息或互相抢占连接。
2. 旧版当前播放和待播队列保存在内存中，不能迁移。请选择队列为空时切换，或者接受切换后队列清空。
3. 正在运行的 JM 任务不能断点迁移。先等待任务完成，再执行正式切换。
4. 旧 JM 压缩包可以手动保留，但不要把整个旧 `jm-tasks` 当成新任务队列导入。
5. QQ 群文件容量已满时，Docker 版本同样无法上传。面板中的“上传器在线”只表示本地上传程序和依赖正常，不代表群文件空间充足。
6. 新面板默认只监听服务器的 `127.0.0.1:3000`，并要求设置用户名和密码。

## 如果服务器没有 Docker：Ubuntu 24.04 安装步骤

当前服务器日志显示系统是 Ubuntu 24.04 LTS。下面使用 Docker 官方 apt 仓库，同时安装 Docker Engine、CLI、containerd、Buildx 和 Compose 插件。官方参考：[Install Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) 和 [Install the Docker Compose plugin](https://docs.docker.com/compose/install/linux/)。

不要安装旧的独立 `docker-compose` 命令。本项目使用新版的 `docker compose` 子命令。

### 1. 确认系统和架构

```bash
cat /etc/os-release
dpkg --print-architecture
uname -m
```

系统应显示 Ubuntu 24.04，常见架构为 `amd64`/`x86_64`。

### 2. 检查是否存在冲突包

```bash
dpkg -l docker.io docker-compose docker-compose-v2 docker-doc docker-buildx podman-docker containerd runc 2>/dev/null | grep '^ii' || true
```

如果没有输出，可以继续。如果有输出，说明系统已经装过发行版 Docker、Podman 兼容层或独立 containerd；不要盲目覆盖，先确认这些组件没有被其他服务使用，再按照 Docker 官方文档移除冲突包。

### 3. 添加 Docker 官方 GPG Key 和 apt 仓库

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

添加官方软件源：

```bash
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
```

确认 apt 已经读到 Docker 官方仓库：

```bash
apt-cache policy docker-ce
```

`docker-ce` 的候选版本来源应包含 `download.docker.com/linux/ubuntu`。

### 4. 安装 Docker Engine 和 Compose

```bash
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

确认服务：

```bash
sudo systemctl status docker --no-pager
docker version
docker compose version
docker buildx version
```

当前登录用户是 `root` 时不需要配置 `docker` 用户组。以后如果要让普通用户直接执行 Docker，需要知道：加入 `docker` 用户组等同于授予接近 root 的权限，不要随意添加不受信任的账号。

### 5. 运行官方测试容器

```bash
sudo docker run --rm hello-world
```

看到 `Hello from Docker!` 即表示 Engine 可以拉取并运行容器。

再做一次 Compose 能力检查：

```bash
docker compose version
docker info --format 'Docker Server {{.ServerVersion}}，存储驱动 {{.Driver}}'
```

### 6. Docker 与防火墙注意事项

Docker 发布的容器端口可能绕过部分 UFW 规则。本项目的面板端口必须保持：

```dotenv
OOPZ_PANEL_BIND=127.0.0.1
```

不要改成 `0.0.0.0` 直接暴露到公网。QQMusic 和内部 Bot 桥接没有映射到宿主机，只在 Compose 私有网络中通信。

Docker 安装完成后，继续执行下一节的旧版本备份与迁移步骤。

## 第一阶段：盘点和备份旧版本

以下操作不会停止旧机器人。

### 1. 记录旧进程

```bash
sudo systemctl status oopzbot --no-pager
sudo systemctl cat oopzbot
sudo systemctl list-units --type=service --all | grep -Ei 'oopz|qqbot|qqmusic' || true
pgrep -af '/home/oopzbot/Oopzbot|/home/oopzbot/qqmusic-api|qqbot_service.py'
```

当前服务器上应当能看到类似下面三个命令，PID 会变化：

```text
/home/oopzbot/Oopzbot/.venv/bin/python /home/oopzbot/Oopzbot/main.py
/home/oopzbot/qqbot-venv/bin/python /home/oopzbot/Oopzbot/qqbot_service.py
/usr/bin/node /home/oopzbot/qqmusic-api/server.mjs
```

把实际启动方式记下来。特别是后两个进程如果不是 systemd 服务，回滚时需要使用原来的启动方式。

### 2. 建立只允许 root 访问的备份目录

```bash
migration_stamp=$(date +%Y%m%d-%H%M%S)
sudo install -d -m 0700 "/root/oopzbot-migration-${migration_stamp}"
sudo cp -a /etc/systemd/system/oopzbot.service "/root/oopzbot-migration-${migration_stamp}/"
sudo cp -a /home/oopzbot/Oopzbot "/root/oopzbot-migration-${migration_stamp}/Oopzbot"
```

如果 `systemctl cat oopzbot` 显示了 `EnvironmentFile=`，还要把对应文件复制到该备份目录。例如：

```bash
sudo cp -a /etc/oopzbot.env "/root/oopzbot-migration-${migration_stamp}/"
```

如果 QQ Bot 的环境变量只存在于进程中，不要把它们直接打印到聊天或公开终端记录。可以把完整环境保存进 root 专用备份文件：

```bash
qqbot_old_pid=$(pgrep -f '/home/oopzbot/Oopzbot/qqbot_service.py' | head -1)
sudo sh -c "tr '\0' '\n' </proc/${qqbot_old_pid}/environ >'/root/oopzbot-migration-${migration_stamp}/qqbot.environ'"
sudo chmod 0600 "/root/oopzbot-migration-${migration_stamp}/qqbot.environ"
```

如需保留失败的 JM 文件，可另外复制：

```bash
sudo cp -a /home/oopzbot/jm-tasks "/root/oopzbot-migration-${migration_stamp}/jm-tasks"
```

## 第二阶段：准备 Docker 新版本

这一阶段可以在旧机器人继续运行时完成，但不要执行 `docker compose up`。

### 1. 检查 Docker

```bash
docker version
docker compose version
df -h
free -h
```

要求 Docker Engine 24 或更高版本，并支持 `docker compose` 子命令。若命令不存在，请先执行本文前面的“Ubuntu 24.04 安装步骤”。

这台 VPS 内存较小。构建前确认有足够磁盘和交换空间；如果并行构建出现 OOM，可以按后文逐个构建镜像。

### 2. 放置新代码

推荐使用独立目录：

```bash
sudo install -d -o root -g root /opt/oopz-music-bot
cd /opt
git clone --branch codex/interactive-installer-qqmusic --single-branch \
  https://github.com/ji333abc/oopz-music-bot.git oopz-music-bot
cd /opt/oopz-music-bot
```

如果目录已经存在，则执行：

```bash
cd /opt/oopz-music-bot
git pull
```

如果新版本还没有推送到 Git，请把本次完整项目目录上传到 `/opt/oopz-music-bot`。上传后至少应存在：

```bash
test -f compose.yaml
test -f Dockerfile
test -f Dockerfile.qqmusic
test -f panel/Dockerfile
test -f oopzbot/qqbot.py
```

不要上传本地的 `node_modules`、`.venv`、`dist`、日志或真实 `.env`。

### 3. 生成新配置

```bash
cd /opt/oopz-music-bot
python3 scripts/init_config.py
chmod 0600 .env
nano .env
```

`init_config.py` 会生成新的内部 `QQBOT_BRIDGE_TOKEN`。不要把旧进程的内部桥接 Token 当作 QQ Secret，也不要在聊天中发送 `.env` 内容。

### 4. 从旧配置迁移必要值

不要整份复制旧环境文件。按下表把值填入新 `.env`：

| 新变量 | 来源或用途 |
| --- | --- |
| `QQBOT_APP_ID` | 原 QQ 官方机器人 AppID |
| `QQBOT_APP_SECRET` | 原 QQ 官方机器人 Secret |
| `QQBOT_ALLOWED_GROUP_OPENIDS` | 允许使用机器人的 QQ 群 OpenID，多个用英文逗号分隔 |
| `OOPZ_LOGIN_PHONE`、`OOPZ_LOGIN_PASSWORD` | 推荐的 OOPZ 登录方式 |
| `OOPZ_DEVICE_ID`、`OOPZ_PERSON_UID`、`OOPZ_JWT_TOKEN` | 如果不用账号密码，则三个一起迁移 |
| `QQBOT_OOPZ_AREA_ID` | 原 OOPZ 域 ID |
| `QQBOT_OOPZ_TEXT_CHANNEL_ID` | 原播放通知文字频道 ID |
| `QQBOT_OOPZ_VOICE_CHANNEL_ID` | 原音乐语音频道 ID |
| `QQ_MUSIC_COOKIE` | 原 QQ 音乐 Cookie；没有可以先留空 |
| `QQ_MUSIC_QUALITY` | 默认 `320` |
| `QQBOT_JM_ENABLED` | 需要 JM 时设置为 `true` |
| `QQBOT_JM_ALLOWED_USER_OPENIDS` | 允许使用 JM 的 QQ 用户 OpenID |
| `OOPZ_PANEL_USERNAME` | 面板用户名，默认 `admin` |
| `OOPZ_PANEL_PASSWORD` | 必填，设置新的高强度密码 |
| `RACKNERD_API_KEY`、`RACKNERD_API_HASH` | 可选，用于面板服务器资源卡片 |

如果旧版使用的是 `OOPZ_AREA_ID`、`OOPZ_TEXT_CHANNEL_ID` 或 `OOPZ_VOICE_CHANNEL_ID`，必须改成表中的 `QQBOT_OOPZ_*` 新名称。

不要把下面这些旧版宿主机绝对路径写进新 `.env`：

```text
QQBOT_JM_PYTHON=/home/oopzbot/jmcomic-venv/bin/python
QQBOT_JM_WORKER=/home/oopzbot/Oopzbot/jmcomic_worker.py
QQBOT_JM_NODE=...
QQBOT_JM_UPLOADER=...
QQBOT_JM_TEMP_ROOT=/home/oopzbot/jm-tasks
QQ_MUSIC_BASE_URL=http://127.0.0.1:3200
QQ_MUSIC_SERVICE_DIR=/home/oopzbot/qqmusic-api
```

容器会使用镜像内的 Python、JM Worker、Node.js、上传器和内部 QQMusic 地址。保留旧绝对路径会导致容器找不到文件。

建议的新 `.env` 关键部分如下，真实值自行填写：

```dotenv
QQBOT_APP_ID=
QQBOT_APP_SECRET=
# QQBOT_BRIDGE_TOKEN 这一行保留初始化脚本生成的随机值
QQBOT_ALLOWED_GROUP_OPENIDS=

OOPZ_LOGIN_METHOD=auto
OOPZ_LOGIN_PHONE=
OOPZ_LOGIN_PASSWORD=

QQBOT_OOPZ_AREA_ID=
QQBOT_OOPZ_TEXT_CHANNEL_ID=
QQBOT_OOPZ_VOICE_CHANNEL_ID=

QQ_MUSIC_ENABLED=true
QQ_MUSIC_COOKIE=
QQ_MUSIC_QUALITY=320
QQ_MUSIC_FALLBACK_QUALITY=128

OOPZ_PANEL_BIND=127.0.0.1
OOPZ_PANEL_PORT=3000
OOPZ_PANEL_USERNAME=admin
OOPZ_PANEL_PASSWORD=

QQBOT_JM_ENABLED=true
QQBOT_JM_ALLOWED_USER_OPENIDS=
```

### 5. 准备持久化目录

```bash
cd /opt/oopz-music-bot
install -d -m 0750 data
```

Compose 会把 `./data` 挂载到容器的 `/app/data`。下列内容会保存在这里：

- JM 临时任务和失败保留文件；
- JM 耗时统计；
- 面板 JM 历史、组件状态和操作事件。

旧版如果存在 `/home/oopzbot/Oopzbot/data/jm_timing.json`，可以迁移它：

```bash
sudo cp -a /home/oopzbot/Oopzbot/data/jm_timing.json /opt/oopz-music-bot/data/jm_timing.json
```

不要迁移旧虚拟环境、Playwright 缓存、QQMusic API 的 `node_modules` 或正在运行的任务目录。

### 6. 检查配置并预构建

```bash
cd /opt/oopz-music-bot
docker compose config >/dev/null
docker compose build
```

低内存服务器可以逐个构建：

```bash
docker compose build qqmusic
docker compose build bot
docker compose build panel
```

构建镜像不会登录 QQ 或 OOPZ，所以此时旧机器人可以继续运行。

## 第三阶段：正式切换

选择没有播放和 JM 任务的时间窗口执行。

### 1. 最后确认旧队列和任务

在 QQ 群或 OOPZ 中确认：

- 当前歌曲已经播放完；
- 待播队列为空；
- 没有 JM 下载或上传任务；
- QQ 群文件仍有可用空间。

### 2. 停止旧版三类进程

先停止 systemd 主服务：

```bash
sudo systemctl disable --now oopzbot
```

再次列出遗留进程：

```bash
pgrep -af '/home/oopzbot/Oopzbot|/home/oopzbot/qqmusic-api|qqbot_service.py'
```

如果仍看到旧 `qqbot_service.py` 或 `server.mjs`，先确认完整命令确实属于旧部署，再对查到的准确 PID 发送 `TERM`。例如：

```bash
sudo kill -TERM QQBOT_旧进程PID
sudo kill -TERM QQMUSIC_旧进程PID
```

不要直接复制示例中的文字占位符，也不要使用宽泛的 `pkill python` 或 `killall node`，以免停止服务器上的其他程序。

等待几秒后确认旧进程已经退出：

```bash
pgrep -af '/home/oopzbot/Oopzbot|/home/oopzbot/qqmusic-api|qqbot_service.py' || true
sudo ss -ltnp | grep -E ':(3000|3200|18080)\b' || true
```

### 3. 启动 Docker 新版

```bash
cd /opt/oopz-music-bot
docker compose up -d
docker compose ps
```

首次启动时依赖的健康检查可能需要一到两分钟。查看日志：

```bash
docker compose logs --tail=200 qqmusic
docker compose logs --tail=200 bot
docker compose logs --tail=200 panel
```

持续查看 Bot 日志：

```bash
docker compose logs -f bot
```

正常情况下应看到 QQ 机器人上线、OOPZ SDK 就绪和内部接口启动，不应持续出现认证失败或反复重连。

## 第四阶段：验证新版本

### 1. 检查容器健康

```bash
cd /opt/oopz-music-bot
docker compose ps
curl -fsS http://127.0.0.1:3000/api/health
docker compose exec bot oopzbot check
```

`qqmusic`、`bot`、`panel` 最终都应为 `healthy`。

### 2. 打开管理面板

面板只监听服务器本机。可从自己的电脑建立 SSH 隧道：

```bash
ssh -L 3000:127.0.0.1:3000 root@你的服务器IP
```

然后在本机浏览器打开：

```text
http://127.0.0.1:3000
```

使用 `.env` 中的 `OOPZ_PANEL_USERNAME` 和 `OOPZ_PANEL_PASSWORD` 登录。面板应显示 QQ、OOPZ、QQMusic 和上传器的真实状态，而不是演示数据。

如果通过域名访问，仍应保持 `OOPZ_PANEL_BIND=127.0.0.1`，让 Nginx/Caddy 反向代理到本机端口，并启用 HTTPS。

### 3. 在 QQ 群逐项验证

所有指令都先 `@机器人`：

```text
状态
搜歌 搁浅
选歌 1
面板
删除 1
在线
```

确认搜歌返回 10 首、队列按钮正常、可以删除指定待播歌曲。

然后选择一个较小的 JM ID 测试：

```text
JM 作品ID
```

在 Web 面板确认任务依次经过“读取元数据”“下载与打包”“上传 QQ 群文件”。如果 QQ 被动回复超时，新版会自动尝试主动群消息；QQ 开放平台仍需允许机器人主动消息。

## 回滚到旧版本

如果新版本无法正常工作，先停止容器：

```bash
cd /opt/oopz-music-bot
docker compose down
```

恢复 systemd 主服务：

```bash
sudo systemctl enable --now oopzbot
sudo systemctl status oopzbot --no-pager
```

再按迁移前记录的原命令或原 systemd 单元恢复旧 QQ Bot 和旧 QQMusic API。最后确认只存在一套 QQ Bot：

```bash
pgrep -af '/home/oopzbot/Oopzbot|/home/oopzbot/qqmusic-api|qqbot_service.py'
```

回滚不会删除 `/opt/oopz-music-bot/data`，因此新版本产生的 JM 历史和任务文件仍会保留。

## 稳定运行后的维护

建议新版本稳定运行至少七天后，再考虑归档旧目录和旧 service 文件。不要在刚迁移成功时删除旧环境。

日常更新：

```bash
cd /opt/oopz-music-bot
git pull
docker compose up -d --build
docker compose ps
```

日常日志：

```bash
docker compose logs -f bot
docker compose logs -f panel
```

停止全部新服务：

```bash
docker compose down
```

`docker compose down` 不会删除宿主机的 `./data`。不要添加 `-v`，本项目虽然使用绑定目录，但迁移和排障时没有必要执行卷删除操作。

## 最终检查清单

- [ ] 已保存旧服务文件、旧配置和三类进程的启动方式；
- [ ] 新代码位于独立目录，没有覆盖 `/home/oopzbot/Oopzbot`；
- [ ] `.env` 权限为 `0600`，没有复制旧宿主机绝对路径；
- [ ] 已设置 `OOPZ_PANEL_PASSWORD`；
- [ ] 已确认播放队列为空且没有运行中的 JM 任务；
- [ ] 已停止旧 `main.py`、`qqbot_service.py` 和 `server.mjs`；
- [ ] 三个容器均为 `healthy`；
- [ ] QQ 搜歌、选歌、队列删除和主动消息兜底正常；
- [ ] OOPZ 播放与频道成员查询正常；
- [ ] JM 小任务下载和上传正常；
- [ ] 已验证回滚步骤，但尚未删除旧目录。
