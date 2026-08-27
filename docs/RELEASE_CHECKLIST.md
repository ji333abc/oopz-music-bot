# 发布与回滚检查单

本检查单适用于测试环境和生产发布。发布前保存当前提交、镜像 ID、备份路径和执行时间；
不要以清空 `data/` 或 Redis 数据作为升级步骤。

## 发布前

- [ ] 确认当前提交：`git rev-parse HEAD`
- [ ] 确认版本单一来源：`python -c "import oopzbot; print(oopzbot.__version__)"`
- [ ] 确认 `CHANGELOG.md`、Panel `package.json`/lock 和 Git Tag 使用同一版本。
- [ ] Python 3.11、Python 3.12、Panel 和 Uploader CI 全绿。
- [ ] `python -m unittest discover -s tests -v`
- [ ] `ruff check oopzbot tests`
- [ ] `python -m build`
- [ ] `npm ci --prefix panel && npm run lint --prefix panel && npm test --prefix panel`
- [ ] `npm ci --prefix tools/qqbot-uploader && npm test --prefix tools/qqbot-uploader`
- [ ] 使用假值执行 `docker compose config --quiet`，并确认 Redis `6379`、QQMusic `3200`、桥接 `18080` 没有宿主机端口。
- [ ] `docker compose build bot qqmusic panel`
- [ ] 测试环境先执行备份：

  ```bash
  python scripts/backup.py --data-dir data --output backups/pre-release-$(date -u +%Y%m%dT%H%M%SZ).zip
  ```

## 测试环境验收

- [ ] `docker compose up -d` 后 `redis`、`qqmusic`、`bot`、`panel` 均为 healthy。
- [ ] `curl -fsS http://127.0.0.1:3000/api/health`
- [ ] `curl -i http://127.0.0.1:18080/healthz` 返回 `ok=true`。
- [ ] `curl -i http://127.0.0.1:18080/readyz` 返回组件快照；未就绪时允许返回 503，但不得让进程重启。
- [ ] QQ 验证点歌、搜歌、选歌、队列删除、切歌和停止。
- [ ] OOPZ 验证文字通知和语音播放。
- [ ] Panel 验证登录、状态、搜歌和队列删除。
- [ ] 暂停 QQMusic：Bot 的 `/healthz` 仍正常，`/readyz` 和 Panel 标记 `qqmusic` 异常；恢复后状态回到 `ok`。
- [ ] 暂停 Redis：队列状态为 `degraded`，Bot 不退出；恢复后重新连接。
- [ ] 日志中可用同一个 `command_id` 串起接收、桥接、执行和回复，且不存在 Secret。
- [ ] 在隔离目录完成一次备份、修改数据、校验并恢复；恢复前自动备份可读取。
- [ ] 重启 Docker 服务后四个容器自动启动。
- [ ] 用 `ss -ltn` 或等价工具确认 `6379`、`3200`、`18080` 没有公网监听。

## 发布命令

在测试环境验收完成后，于发布提交上执行：

```bash
git fetch --tags
git pull --ff-only
python scripts/backup.py --data-dir data --output backups/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ).zip
docker compose config --quiet
docker compose build bot qqmusic panel
docker compose up -d
docker compose ps
```

生产环境不得在未完成测试验收或未保存备份路径时执行发布。

## 回滚命令

先记录当前提交和镜像 ID，然后停止服务但保留 `data/` 与 Redis 命名卷：

```bash
git log -1 --format='%H %s'
docker compose ps
docker compose stop
git switch --detach <升级前提交>
docker compose build bot qqmusic panel
docker compose up -d
docker compose ps
```

如果数据也需要恢复，先停止 Bot，再使用已验证归档：

```bash
python scripts/restore.py backups/<verified-backup>.zip --data-dir data --component all --confirm
docker compose up -d
```

回滚不使用 `git reset --hard`，也不删除数据目录或 Redis 卷。完成后重新执行测试环境验收。

## 本次 P0 基线执行记录（2026-08-28）

- [x] Python 单元测试：104 项通过。
- [x] Python `compileall`：通过。
- [x] 备份/恢复隔离测试：4 项通过。
- [x] Docker 静态配置测试：5 项通过。
- [ ] 本机 Docker 镜像构建和四服务测试：未执行；当前工作站未安装 Docker CLI，需在具备 Docker 的测试服务器执行并补录结果。
- [ ] 本机 Panel 构建：未执行；系统 Node.js 为 20.18.0，而 Panel 明确要求 `>=22.13.0`，需用 CI Node 22.13.0 或更高版本执行。

## 配置迁移

本阶段无需修改 `.env`；保持现有字段和值即可。备份默认排除 `.env`，恢复也不会替换它。
