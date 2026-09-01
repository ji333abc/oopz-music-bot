"""Versioned DTOs for the Redis-backed JM worker boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

JM_JOB_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class JMJob:
    job_id: str
    album_id: str
    requester_ref: str
    group_openid: str
    message_id: str
    password: str
    max_archive_bytes: int
    timeout_seconds: int
    schema_version: int = JM_JOB_SCHEMA_VERSION
    status: str = "queued"
    lease_seconds: int = 2100
    result: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{16,64}", self.job_id):
            raise ValueError("invalid JM job id")
        if not re.fullmatch(r"\d{1,12}", self.album_id):
            raise ValueError("invalid JM album id")
        if len(self.password) < 8:
            raise ValueError("JM archive password is too short")
        if self.schema_version != JM_JOB_SCHEMA_VERSION:
            raise ValueError("unsupported JM job schema")
        if not 1 <= int(self.max_archive_bytes) <= 512 * 1024 * 1024:
            raise ValueError("invalid JM archive limit")
        if not 30 <= int(self.timeout_seconds) <= 7200:
            raise ValueError("invalid JM timeout")
        if self.status not in {"queued", "running", "completed", "failed"}:
            raise ValueError("invalid JM status")
        if int(self.lease_seconds) < int(self.timeout_seconds):
            raise ValueError("JM lease must cover the task timeout")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> JMJob:
        return cls(
            job_id=str(value.get("job_id") or ""),
            album_id=str(value.get("album_id") or ""),
            requester_ref=str(value.get("requester_ref") or "")[:80],
            group_openid=str(value.get("group_openid") or "")[:160],
            message_id=str(value.get("message_id") or "")[:160],
            password=str(value.get("password") or ""),
            max_archive_bytes=int(value.get("max_archive_bytes") or 0),
            timeout_seconds=int(value.get("timeout_seconds") or 0),
            schema_version=int(value.get("schema_version") or 0),
            status=str(value.get("status") or "queued"),
            lease_seconds=int(value.get("lease_seconds") or 0),
            result=(
                dict(value.get("result") or {})
                if isinstance(value.get("result"), Mapping)
                else {}
            ),
        )
