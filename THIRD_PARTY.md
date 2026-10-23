# 第三方组件说明

本仓库的业务代码采用 MIT 许可证，但依赖项仍分别适用其上游许可证。

主要直接依赖包括：

- [Oopzbot SDK](https://github.com/tangqingfeng7/Oopzbot-SDK)（MIT）：OOPZ 登录、消息和语音能力。
- [QQ BotPy](https://github.com/tencent-connect/botpy)（MIT）：QQ 官方机器人接入。
- [QQBot Node.js SDK](https://www.npmjs.com/package/@tencent-connect/qqbot-nodejs)（MIT，可选）：群文件上传。
- [JMComic](https://github.com/hect0x7/JMComic-Crawler-Python)（MIT，可选）：文件任务的数据获取与图片处理。
- [sansenjian/qq-music-api](https://github.com/sansenjian/qq-music-api)（MIT）：Docker Compose 使用的 QQ 音乐接口，固定 npm 版本 `@sansenjian/qq-music-api@2.4.0`，与旧版原生部署保持一致。源码和依赖保持在独立容器中。
- FastAPI、Uvicorn、Requests、Playwright 及其余依赖：详见 `pyproject.toml` 和各软件包自身许可证。

B站访客身份、WBI 签名和独立音轨取流流程参考了
[Jmiao11/bili-music](https://github.com/Jmiao11/bili-music) 的实现与验证用例，
采用 Python 接入现有播放器，未引入其 Rust/Tauri 运行时。
其 MIT 版权与许可文本见 [LICENSE.bili-music](LICENSE.bili-music)。

QQ 音乐 API 上游 README 另有“仅供学习、不可商用”的使用提示。默认安装不会改变其作者署名、MIT 许可证或上游声明；音乐内容的访问与使用仍由部署者自行确保合规。

项目名称、平台名称和商标归各自权利人所有。本项目不是 OOPZ、腾讯或 QQ 音乐官方产品。
