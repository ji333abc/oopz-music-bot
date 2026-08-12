# 配置说明

## 创建配置

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

## 目标频道

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `QQBOT_OOPZ_AREA_ID` | 是 | OOPZ 域 ID |
| `QQBOT_OOPZ_TEXT_CHANNEL_ID` | 是 | 播放通知文字频道 ID |
| `QQBOT_OOPZ_VOICE_CHANNEL_ID` | 是 | 音频推送语音频道 ID |

只填写登录信息后运行 `oopzbot discover`，即可打印账号加入的域和频道 ID。

## 音乐接口

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QQ_MUSIC_ENABLED` | `true` | 是否启用 QQ 音乐 |
| `QQ_MUSIC_BASE_URL` | `http://127.0.0.1:3200` | 兼容音乐 API 根地址 |
| `QQ_MUSIC_COOKIE` | 空 | 需要登录态或高音质时填写 |
| `QQ_MUSIC_QUALITY` | `320` | 主音质：`m4a/128/320/ape/flac` |
| `QQ_MUSIC_FALLBACK_QUALITY` | `128` | 主地址不可用时的音质 |

音乐 API 不在本项目内启动。Docker 中访问宿主机服务时使用 `http://host.docker.internal:3200`。

## 可选后台任务

`QQBOT_JM_ENABLED=false` 默认关闭。启用前需要安装 `.[jm]` 和上传器的 npm 依赖，并建议同时配置 `QQBOT_JM_ALLOWED_USER_OPENIDS`。

## 检查

```bash
oopzbot check
```

检查命令不会连接外部服务，也不会输出任何 Secret。
