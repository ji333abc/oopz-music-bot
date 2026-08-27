# 配置说明

## 创建配置

`install.sh` 和 `install.ps1` 会自动启动交互式配置向导。向导提供选项和默认值，并隐藏 App Secret、密码、Token 与 Cookie 的输入内容。

OOPZ 频道配置支持域 ID 联动查询：向导先列出账号加入的域，也允许手动输入域 ID；随后读取该域的频道，并分别选择文字频道和语音频道。

安装完成后可以再次运行向导：

```bash
.venv/bin/python scripts/configure.py
```

```powershell
.\.venv\Scripts\python.exe .\scripts\configure.py
```

也可以直接从模板创建配置：

```bash
python scripts/init_config.py
```

该命令从 `.env.example` 创建 `.env`，同时生成随机内部 Token。它不会覆盖已有 `.env`。

## QQ Bot

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `QQBOT_APP_ID` | 是 | QQ 开放平台机器人 App ID |
| `QQBOT_APP_SECRET` | 是 | QQ 开放平台机器人 App Secret |
| `QQBOT_BRIDGE_TOKEN` | 是 | 初始化脚本自动生成 |
| `QQBOT_ALLOWED_GROUP_OPENIDS` | 否 | 逗号分隔；为空表示不限制群 |
| `QQBOT_COMMAND_DEFER_SECONDS` | 否 | 默认 `2.5`；命令超过该时长时先回复处理中，完成后主动发送结果 |

## OOPZ 登录

推荐使用账号密码：

```dotenv
OOPZ_LOGIN_METHOD=auto
OOPZ_LOGIN_PHONE=
OOPZ_LOGIN_PASSWORD=
```

也可以使用已有凭据，此时三个值必须同时填写：

```dotenv
OOPZ_DEVICE_ID=
OOPZ_PERSON_UID=
OOPZ_JWT_TOKEN=
```

不要同时保留一组过期的部分凭据；配置检查会将其视为错误。

## 旧版 OOPZ 核心

Docker Compose 默认启用迁移前的完整 OOPZ 核心：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `OOPZBOT_USE_LEGACY_CORE` | `true`（Compose） | 使用旧版消息、音乐、Sender、WebSocket 和 Agora 核心 |
| `OOPZ_AGORA_APP_ID` | 空 | 必填；旧 `config.py` 中 `OOPZ_CONFIG.agora_app_id` 的值 |
| `OOPZ_AGORA_INIT_TIMEOUT` | `180` | 无头 Chromium/Agora 初始化等待秒数 |
| `OOPZ_LEGACY_WEB_BIND` | `127.0.0.1` | 旧 Web 播放页映射到宿主机的监听地址 |
| `OOPZ_LEGACY_WEB_PORT` | `18081` | 旧 Web 播放页端口；新管理面板仍是 `3000` |
| `OOPZ_LEGACY_ADMIN_ENABLED` | `false` | 是否启用旧版管理页面；通常使用新面板即可 |
| `OOPZ_LEGACY_ADMIN_PASSWORD` | 空 | 启用旧版管理页面时必须设置 |

账号密码登录成功后，刷新得到的 OOPZ 凭据和 RSA 私钥会写入 `data/legacy/`，容器重建后继续使用，不会写回镜像源码。若只迁移静态 `DEVICE_ID/PERSON_UID/JWT_TOKEN`，还必须把旧 RSA 私钥保存为 `data/legacy/private_key.pem`，权限设为 `0600`。

Compose 同时启动 Redis 并启用 AOF。播放队列、当前播放和播放状态按 OOPZ 域隔离保存。不要执行 `docker compose down -v`，否则会删除 Redis 数据卷。

## 目标频道

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `QQBOT_OOPZ_AREA_ID` | 是 | OOPZ 域 ID |
| `QQBOT_OOPZ_TEXT_CHANNEL_ID` | 是 | 播放通知文字频道 ID |
| `QQBOT_OOPZ_VOICE_CHANNEL_ID` | 是 | 音频推送语音频道 ID |

只填写登录信息后运行 `oopzbot discover`，即可打印账号加入的域和频道 ID。

查询指定域：

```bash
oopzbot discover --area-id <域ID>
```

## 音乐接口

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QQ_MUSIC_ENABLED` | `true` | 是否启用 QQ 音乐 |
| `QQ_MUSIC_MANAGED` | `true` | 是否由 Bot 自动启动安装脚本提供的固定版本 API |
| `QQ_MUSIC_BASE_URL` | `http://127.0.0.1:3200` | 音乐 API 根地址 |
| `QQ_MUSIC_SERVICE_DIR` | `.services/qqmusic-api` | 固定版本 API 的本地安装目录 |
| `QQ_MUSIC_COOKIE` | 空 | 需要登录态或高音质时填写 |
| `QQ_MUSIC_QUALITY` | `320` | 主音质：`m4a/128/320/ape/flac` |
| `QQ_MUSIC_FALLBACK_QUALITY` | `128` | 主地址不可用时的音质 |

本地安装保持前三项默认值即可。Bot 会验证安装标记、固定提交和四个必需端点，然后在回环地址启动服务；QQ/OOPZ 凭据不会传给该子进程。

只有明确使用自行维护的服务时才设置 `QQ_MUSIC_MANAGED=false` 并修改 `QQ_MUSIC_BASE_URL`。外部服务必须兼容 `/getSearchByKey`、`/getMusicPlay`、`/getSongInfo` 和 `/getLyric`。

## 可选后台任务

`QQBOT_JM_ENABLED=false` 默认关闭。启用前需要安装 `.[jm]` 和上传器的 npm 依赖，并建议同时配置 `QQBOT_JM_ALLOWED_USER_OPENIDS`。

## 检查

```bash
oopzbot check
```

检查命令不会连接外部服务，也不会输出任何 Secret。
