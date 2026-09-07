"""面板使用的轻量运行记录与组件状态存储。"""

from __future__ import annotations

import json
import math
import os
import re
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from .metrics import (
    CommandTiming,
    FailureRecord,
    LatencySummary,
    PlaybackHistoryItem,
)
from .observability import redact_secrets

STATE_SCHEMA_VERSION = 2
COMPONENT_STORAGE_LIMIT = 64
EVENT_STORAGE_LIMIT = 100
JM_JOB_STORAGE_LIMIT = 50
def _bounded_limit(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if 10 <= value <= maximum else default


PLAYBACK_HISTORY_LIMIT = _bounded_limit("OOPZ_PLAYBACK_HISTORY_LIMIT", 50, 500)
COMMAND_HISTORY_LIMIT = _bounded_limit("OOPZ_COMMAND_HISTORY_LIMIT", 200, 2000)
FAILURE_HISTORY_LIMIT = _bounded_limit("OOPZ_FAILURE_HISTORY_LIMIT", 100, 1000)
EXTERNAL_METRIC_SERIES_LIMIT = 64
PANEL_HISTORY_VIEW_LIMIT = 30
MAX_STATE_BYTES = 512 * 1024

COMPONENT_STATUSES = frozenset(
    {"starting", "ok", "degraded", "error", "offline", "unknown"}
)
_STATUS_ALIASES = {"online": "ok", "disabled": "offline"}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_URL = re.compile(r"(?i)https?://[^\s<>'\"]+")


def _redact_state(value: object, *, max_length: int) -> str:
    """State files never retain raw media or signed dependency URLs."""
    return _URL.sub("[URL_REDACTED]", redact_secrets(value, max_length=max_length))


def _safe_number(value: object, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return int(number) if integer else number


def _sanitize_state(data: dict) -> None:
    raw_components = data.get("components", {})
    sanitized_components = {}
    for raw_name, component in list(raw_components.items())[-COMPONENT_STORAGE_LIMIT:]:
        if not isinstance(component, dict):
            continue
        name = _redact_state(raw_name, max_length=80)
        reason = _redact_state(
            component.get("reason") or component.get("message") or "未知原因",
            max_length=240,
        )
        sanitized_components[name] = {
            "status": _redact_state(component.get("status"), max_length=20),
            "reason": reason,
            "message": reason,
            "updated_at": _redact_state(component.get("updated_at"), max_length=64),
        }
    data["components"] = sanitized_components
    for event in data.get("events", []):
        if not isinstance(event, dict):
            continue
        for key, maximum in {
            "id": 80,
            "type": 80,
            "message": 500,
            "level": 20,
            "source": 80,
            "created_at": 64,
        }.items():
            event[key] = _redact_state(event.get(key), max_length=maximum)
    for job in data.get("jm_jobs", []):
        if not isinstance(job, dict):
            continue
        for key, maximum in {
            "id": 80,
            "album_id": 80,
            "status": 40,
            "phase": 40,
            "error": 500,
            "requester": 80,
            "started_at": 64,
            "updated_at": 64,
            "completed_at": 64,
        }.items():
            value = job.get(key)
            job[key] = (
                None
                if key == "completed_at" and value is None
                else _redact_state(value, max_length=maximum)
            )
        for key in ("page_count", "archive_bytes", "batch_index", "batch_total"):
            job[key] = _safe_number(job.get(key), integer=True)
    for item in data.get("playback_history", []):
        if not isinstance(item, dict):
            continue
        for key, maximum in {
            "song_id": 160,
            "name": 200,
            "artists": 200,
            "platform": 40,
            "source": 80,
            "result": 40,
            "error_kind": 80,
            "started_at": 64,
            "ended_at": 64,
        }.items():
            item[key] = _redact_state(item.get(key), max_length=maximum)
    for item in data.get("command_history", []):
        if not isinstance(item, dict):
            continue
        for key, maximum in {
            "command_id": 80,
            "source": 80,
            "kind": 80,
            "error_kind": 80,
            "created_at": 64,
        }.items():
            item[key] = _redact_state(item.get(key), max_length=maximum)
        item["ok"] = bool(item.get("ok"))
        item["duration_ms"] = _safe_number(item.get("duration_ms")) or 0.0
    for item in data.get("failure_history", []):
        if not isinstance(item, dict):
            continue
        for key, maximum in {
            "component": 80,
            "error_kind": 80,
            "message": 500,
            "command_id": 80,
            "created_at": 64,
        }.items():
            item[key] = _redact_state(item.get(key), max_length=maximum)
    external_metrics = data.get("external_metrics", {})
    if isinstance(external_metrics, dict):
        sanitized_metrics = {}
        for raw_key, raw_value in list(external_metrics.items())[-EXTERNAL_METRIC_SERIES_LIMIT:]:
            if not isinstance(raw_value, dict):
                continue
            key = _redact_state(raw_key, max_length=160)
            sanitized_metrics[key] = {
                "count": _safe_number(raw_value.get("count"), integer=True) or 0,
                "success": _safe_number(raw_value.get("success"), integer=True) or 0,
                "failure": _safe_number(raw_value.get("failure"), integer=True) or 0,
                "last_ms": _safe_number(raw_value.get("last_ms")),
                "p50_ms": _safe_number(raw_value.get("p50_ms")),
                "p95_ms": _safe_number(raw_value.get("p95_ms")),
                "success_rate": _safe_number(raw_value.get("success_rate")),
                "result_counts": {
                    _redact_state(result, max_length=80): (
                        _safe_number(count, integer=True) or 0
                    )
                    for result, count in list(
                        (raw_value.get("result_counts") or {}).items()
                    )[:20]
                }
                if isinstance(raw_value.get("result_counts"), dict)
                else {},
            }
        data["external_metrics"] = sanitized_metrics


def _trim_state(data: dict) -> None:
    limits = {
        "events": EVENT_STORAGE_LIMIT,
        "jm_jobs": JM_JOB_STORAGE_LIMIT,
        "playback_history": PLAYBACK_HISTORY_LIMIT,
        "command_history": COMMAND_HISTORY_LIMIT,
        "failure_history": FAILURE_HISTORY_LIMIT,
    }
    for key, limit in limits.items():
        values = data.get(key)
        data[key] = values[-limit:] if isinstance(values, list) else []
    components = data.get("components")
    if not isinstance(components, dict):
        data["components"] = {}
    elif len(components) > COMPONENT_STORAGE_LIMIT:
        data["components"] = dict(
            list(components.items())[-COMPONENT_STORAGE_LIMIT:]
        )
    metrics = data.get("external_metrics")
    if not isinstance(metrics, dict):
        data["external_metrics"] = {}
    elif len(metrics) > EXTERNAL_METRIC_SERIES_LIMIT:
        data["external_metrics"] = dict(
            list(metrics.items())[-EXTERNAL_METRIC_SERIES_LIMIT:]
        )
    data["version"] = STATE_SCHEMA_VERSION
    data["schema_version"] = STATE_SCHEMA_VERSION


class OperationsRegistry:
    """保存少量运维状态；配置文件路径后会原子写入持久卷。"""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = str(path or os.getenv("OOPZBOT_PANEL_STATE_FILE") or "").strip()
        self._path = Path(configured) if configured else None
        self._lock = RLock()
        self._data: dict = {
            "version": STATE_SCHEMA_VERSION,
            "schema_version": STATE_SCHEMA_VERSION,
            "components": {},
            "events": [],
            "jm_jobs": [],
            "playback_history": [],
            "command_history": [],
            "failure_history": [],
            "external_metrics": {},
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
            for key in (
                "components",
                "events",
                "jm_jobs",
                "playback_history",
                "command_history",
                "failure_history",
                "external_metrics",
            ):
                if isinstance(value.get(key), type(self._data[key])):
                    self._data[key] = value[key]
            _sanitize_state(self._data)
            _trim_state(self._data)
            # Older state files may contain messages written before centralized
            # redaction existed. Rewrite the sanitized form when possible so a
            # later backup cannot preserve those historical secret values.
            try:
                self._save()
            except OSError:
                pass

    def _save(self) -> None:
        # Notify the in-process Panel stream after every semantic registry
        # mutation. The publisher retains revisions only, never full snapshots.
        from .state_publisher import state_publisher

        state_publisher.publish()
        if self._path is None:
            return
        _sanitize_state(self._data)
        _trim_state(self._data)
        serialized = json.dumps(self._data, ensure_ascii=False, indent=2)
        if len(serialized.encode("utf-8")) > MAX_STATE_BYTES:
            raise ValueError("panel state exceeds bounded storage limit")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        os.replace(temporary, self._path)

    def set_component(self, name: str, status: str, message: str) -> None:
        normalized_status = _STATUS_ALIASES.get(str(status).strip().lower(), str(status).strip().lower())
        if normalized_status not in COMPONENT_STATUSES:
            raise ValueError(
                f"组件状态必须是 {', '.join(sorted(COMPONENT_STATUSES))}"
            )
        reason = _redact_state(" ".join(str(message or "未知原因").split()), max_length=240)
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
            "type": _redact_state(event_type, max_length=80),
            "message": _redact_state(message, max_length=500),
            "level": _redact_state(level, max_length=20),
            "source": _redact_state(source, max_length=80),
            "created_at": _now(),
        }
        with self._lock:
            self._data["events"].append(event)
            self._data["events"] = self._data["events"][-EVENT_STORAGE_LIMIT:]
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
            "phase": "submitting",
            "page_count": None,
            "archive_bytes": None,
            "error": "",
            "requester": _redact_state(requester, max_length=80),
            "batch_index": batch_index,
            "batch_total": batch_total,
            "started_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        with self._lock:
            self._data["jm_jobs"].append(job)
            self._data["jm_jobs"] = self._data["jm_jobs"][-JM_JOB_STORAGE_LIMIT:]
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
                        _redact_state(value, max_length=500)
                        if key == "error"
                        else value
                    )
            job["updated_at"] = _now()
            if job.get("status") in {"completed", "failed", "timeout"}:
                job["completed_at"] = job["updated_at"]
            self._save()

    def record_command_timing(self, timing: CommandTiming) -> None:
        item = asdict(timing)
        item["created_at"] = str(item.get("created_at") or _now())
        item["duration_ms"] = _safe_number(item.get("duration_ms")) or 0.0
        with self._lock:
            self._data["command_history"].append(item)
            self._data["command_history"] = self._data["command_history"][
                -COMMAND_HISTORY_LIMIT:
            ]
            self._save()

    def record_playback(self, item: PlaybackHistoryItem) -> None:
        value = asdict(item)
        with self._lock:
            self._data["playback_history"].append(value)
            self._data["playback_history"] = self._data["playback_history"][
                -PLAYBACK_HISTORY_LIMIT:
            ]
            self._save()

    def record_failure(self, failure: FailureRecord) -> None:
        item = asdict(failure)
        item["created_at"] = str(item.get("created_at") or _now())
        with self._lock:
            self._data["failure_history"].append(item)
            self._data["failure_history"] = self._data["failure_history"][
                -FAILURE_HISTORY_LIMIT:
            ]
            self._save()

    def set_external_metric(
        self,
        service: str,
        operation: str,
        summary: LatencySummary,
    ) -> None:
        key = f"{_redact_state(service, max_length=80)}:{_redact_state(operation, max_length=80)}"
        with self._lock:
            metrics = self._data["external_metrics"]
            metrics.pop(key, None)
            metrics[key] = summary.to_dict()
            if len(metrics) > EXTERNAL_METRIC_SERIES_LIMIT:
                oldest = next(iter(metrics))
                metrics.pop(oldest, None)
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            value = deepcopy(self._data)
        _sanitize_state(value)
        _trim_state(value)
        for key in (
            "events",
            "jm_jobs",
            "playback_history",
            "command_history",
            "failure_history",
        ):
            value[key] = list(reversed(value[key][-PANEL_HISTORY_VIEW_LIMIT:]))
        return value


operations = OperationsRegistry()
