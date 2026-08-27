# Changelog

本项目遵循 Keep a Changelog 风格。当前发布版本由 `oopzbot.__version__` 提供，Panel
`package.json` 和 Git Tag 必须在发布前与它保持一致。

## [0.1.0] - 2026-08-28

### Added

- P0 稳定性基线 CI：Python 3.11/3.12、Panel、QQ 上传器和 Docker/Compose 验证。
- Bot `/healthz` 存活接口、`/readyz` 就绪接口和统一组件快照。
- QQ 与 Panel 命令的 `command_id` 关联日志，以及日志 Secret 脱敏。
- 本地 Fake/Stub 外部依赖故障测试。
- `scripts/backup.py` 和 `scripts/restore.py` 数据、Redis 快照备份恢复工具。

### Changed

- Panel 现在消费 Bot 统一组件快照，并显示有限状态：`starting`、`ok`、`degraded`、`error`、`offline`、`unknown`。
- QQMusic 的 HTTP 404、超时和网络故障返回具体的音乐错误。

### Fixed

- Redis 内存降级、QQMusic 空播放地址、JM 下载失败等路径的状态和错误可定位。
- 恢复流程会在写入前验证校验清单，并自动保存当前数据。

### Security

- 日志不输出 AppSecret、Bridge Token、Cookie、JWT、密码、私钥和 RackNerd 凭据。
- 备份默认不包含 `.env`，恢复必须显式提供 `--confirm`。
- Redis、QQMusic 和 Bot 桥接端口继续只在 Compose 私有网络中开放。

### Migration notes

- 本阶段不修改 `.env` 字段；现有配置保持兼容。
- 升级后可用 `/healthz`、`/readyz` 和 Panel 组件状态检查运行状况。
- 详见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)、[docs/MIGRATION_FROM_LEGACY.md](docs/MIGRATION_FROM_LEGACY.md) 和 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)。
