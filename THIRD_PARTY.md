# 第三方组件说明

本仓库的业务代码采用 MIT 许可证，但依赖项仍分别适用其上游许可证。

主要直接依赖包括：

- [Oopzbot SDK](https://github.com/tangqingfeng7/Oopzbot-SDK)（MIT）：OOPZ 登录、消息和语音能力。
- [QQ BotPy](https://github.com/tencent-connect/botpy)（MIT）：QQ 官方机器人接入。
- [QQBot Node.js SDK](https://www.npmjs.com/package/@tencent-connect/qqbot-nodejs)（MIT，可选）：群文件上传。
- [JMComic](https://github.com/hect0x7/JMComic-Crawler-Python)（MIT，可选）：文件任务的数据获取与图片处理。
- FastAPI、Uvicorn、Requests、Playwright 及其余依赖：详见 `pyproject.toml` 和各软件包自身许可证。

`QQ_MUSIC_BASE_URL` 指向的兼容音乐接口是独立服务，本仓库不包含、修改或再发布其源码。部署者应自行确认所选服务的许可证、接口条款和内容授权。

项目名称、平台名称和商标归各自权利人所有。本项目不是 OOPZ、腾讯或 QQ 音乐官方产品。
