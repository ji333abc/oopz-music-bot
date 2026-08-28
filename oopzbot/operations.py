"""面板使用的轻量运行记录与组件状态存储。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .observability import redact_secrets

COMPONENT_STATUSES = frozenset(
    {"starting", "ok", "degraded", "error", "offline", "unknown"}
)
_STATUS_ALIASES = {"online": "ok", "disabled": "offline"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sanitize_state(data: dict) -> None:
    for component in data.get("components", {}).values():
        if not isinstance(component, dict):
            continue
        reason = redact_secrets(
            component.get("reason") or component.get("message") or "未知原因",
            max_length=240,
        )
        component["reason"] = reason
        component["message"] = reason
    for event in data.get("events", []):
        if not isinstance(event, dict):
            continue
        event["message"] = redact_secrets(event.get("message"), max_length=500)
        event["source"] = redact_secrets(event.get("source"), max_length=80)
    for job in data.get("jm_jobs", []):
        if not isinstance(job, dict):
            continue
        job["error"] = redact_secrets(job.get("error"), max_length=500)
        job["requester"] = redact_secrets(job.get("requester"), max_length=80)


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
            _sanitize_state(self._data)
            # Older state files may contain messages written before centralized
            # redaction existed. Rewrite the sanitized form when possible so a
            # later backup cannot preserve those historical secret values.
            try:
                self._save()
            except OSError:
                pass

    def _save(self) -> None:
        if self._path is None:
            return
        _sanitize_state(self._data)
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
            "type": redact_secrets(event_type, max_length=80),
            "message": redact_secrets(message, max_length=500),
            "level": redact_secrets(level, max_length=20),
            "source": redact_secrets(source, max_length=80),
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
            "requester": redact_secrets(requester, max_length=80),
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
                    job[key] = (
                        redact_secrets(value, max_length=500)
                        if key == "error"
                        else value
                    )
            job["updated_at"] = _now()
            if job.get("status") in {"completed", "failed", "timeout"}:
                job["completed_at"] = job["updated_at"]
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            value = deepcopy(self._data)
        _sanitize_state(value)
        value["events"] = list(reversed(value["events"][-30:]))
        value["jm_jobs"] = list(reversed(value["jm_jobs"][-30:]))
        return value


operations = OperationsRegistry()
