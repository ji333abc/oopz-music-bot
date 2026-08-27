"""面板使用的轻量运行记录与组件状态存储。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .observability import redact_secrets


COMPONENT_STATUSES = frozenset(
    {"starting", "ok", "degraded", "error", "offline", "unknown"}
)
_STATUS_ALIASES = {"online": "ok", "disabled": "offline"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OperationsRegistry:
    """保存少量运维状态；配置文件路径后会原子写入持久卷。"""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = str(path or os.getenv("OOPZBOT_PANEL_STATE_FILE") or "").strip()
        self._path = Path(configured) if configured else None
        self._lock = RLock()
        self._data: dict = {
            "version": 1,
            "components": {},
            "events": [],
            "jm_jobs": [],
        }
        self._load()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(value, dict):
            return
        with self._lock:
            for key in ("components", "events", "jm_jobs"):
                if isinstance(value.get(key), type(self._data[key])):
                    self._data[key] = value[key]

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self._path)

    def set_component(self, name: str, status: str, message: str) -> None:
        normalized_status = _STATUS_ALIASES.get(str(status).strip().lower(), str(status).strip().lower())
        if normalized_status not in COMPONENT_STATUSES:
            raise ValueError(
                f"组件状态必须是 {', '.join(sorted(COMPONENT_STATUSES))}"
            )
        reason = redact_secrets(" ".join(str(message or "未知原因").split()), max_length=240)
        with self._lock:
            self._data["components"][name] = {
                "status": normalized_status,
                "message": reason,
                "reason": reason,
                "updated_at": _now(),
            }
            self._save()

    def record_event(
        self,
        event_type: str,
        message: str,
        *,
        level: str = "info",
        source: str = "system",
    ) -> dict:
        event = {
            "id": uuid4().hex,
            "type": event_type,
            "message": str(message)[:500],
            "level": level,
            "source": str(source)[:80],
            "created_at": _now(),
        }
        with self._lock:
            self._data["events"].append(event)
            self._data["events"] = self._data["events"][-100:]
            self._save()
        return deepcopy(event)

    def begin_jm_job(
        self,
        album_id: str,
        *,
        requester: str = "QQ 群",
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> str:
        now = _now()
        job = {
            "id": uuid4().hex,
            "album_id": str(album_id),
            "status": "running",
            "phase": "inspecting",
            "page_count": None,
            "archive_bytes": None,
            "error": "",
            "requester": str(requester)[:80],
            "batch_index": batch_index,
            "batch_total": batch_total,
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        with self._lock:
            self._data["jm_jobs"].append(job)
            self._data["jm_jobs"] = self._data["jm_jobs"][-50:]
            self._save()
        self.record_event("jm", f"JM{album_id} 开始处理", source=requester)
        return str(job["id"])

    def update_jm_job(self, job_id: str, **changes) -> None:
        safe_keys = {
            "status",
            "phase",
            "page_count",
            "archive_bytes",
            "error",
        }
        with self._lock:
            job = next(
                (item for item in self._data["jm_jobs"] if item.get("id") == job_id),
                None,
            )
            if job is None:
                return
            for key, value in changes.items():
                if key in safe_keys:
                    job[key] = str(value)[:500] if key == "error" else value
            job["updated_at"] = _now()
            if job.get("status") in {"completed", "failed", "timeout"}:
                job["completed_at"] = job["updated_at"]
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            value = deepcopy(self._data)
        value["events"] = list(reversed(value["events"][-30:]))
        value["jm_jobs"] = list(reversed(value["jm_jobs"][-30:]))
        return value


operations = OperationsRegistry()
