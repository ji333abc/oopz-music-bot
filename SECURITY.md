# 安全政策

## 报告问题

请通过 GitHub Security Advisory 或仓库所有者指定的私密联系方式报告安全问题。请勿在公开 Issue 中粘贴 Token、Cookie、App Secret、服务器地址、用户 OpenID 或日志原文。

## 凭据处理

- 真实凭据只放在未跟踪的 `.env`、systemd `EnvironmentFile`、Nginx 私有 snippet 或托管平台的 Secret 中。
- 示例配置只能使用空值和明显的占位符。
- 如果凭据曾经进入 Git 历史，仅删除文件不够；应立即轮换凭据，并清理历史后再公开仓库。
- 发布前应检查源文件、归档包、日志、数据库、截图和构建产物。

## 默认暴露面

- 命令桥接应只监听回环地址，并校验 `QQBOT_BRIDGE_TOKEN`。
- 内部桥接仅供同一主机上的 QQ Bot 进程通信，禁止将其端口转发到公网。
- 本地默认音乐 API 只绑定回环地址，并由 Bot 使用固定提交启动；Docker 版本只在 Compose 内部网络开放。
- 音乐 API 子进程不会继承 QQ Bot、OOPZ 或其他应用 Secret。改用外部 API 时仍需单独审查其安全性和日志策略。
- 可选下载/归档能力应使用用户及群组白名单，并将输出目录放在 Git 仓库之外。
