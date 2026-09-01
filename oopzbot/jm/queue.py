"""Redis queue, lease and result protocol for isolated JM workers."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from .contracts import JMJob

QUEUE_KEY = "oopzbot:jm:jobs:v1"
PROCESSING_KEY = "oopzbot:jm:processing:v1"
HEARTBEAT_KEY = "oopzbot:jm:worker:heartbeat"


@dataclass(frozen=True, slots=True)
class ClaimedJMJob:
    job: JMJob
    raw: str
    lease_token: str = ""


def redis_client():
    import redis

    return redis.Redis(
        host=os.getenv("BOT_REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("BOT_REDIS_PORT", "6379")),
        password=os.getenv("BOT_REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    )


class RedisJMQueue:
    def __init__(self, client=None) -> None:
        self.redis = client or redis_client()

    @staticmethod
    def _lease_key(job_id: str) -> str:
        return f"oopzbot:jm:lease:{job_id}"

    @staticmethod
    def _result_key(job_id: str) -> str:
        return f"oopzbot:jm:result:{job_id}"

    def available(self) -> bool:
        try:
            return bool(self.redis.get(HEARTBEAT_KEY))
        except Exception:
            return False

    def heartbeat(self) -> None:
        self.redis.set(HEARTBEAT_KEY, str(int(time.time())), ex=15)

    def recover_stale(self) -> int:
        """Requeue crash leftovers only after their lease has expired."""
        recovery_key = "oopzbot:jm:recovery-lock"
        if not self.redis.set(recovery_key, "1", nx=True, ex=30):
            return 0
        recovered = 0
        try:
            for raw in self.redis.lrange(PROCESSING_KEY, 0, -1):
                try:
                    job = JMJob.from_dict(json.loads(raw))
                except Exception:
                    self.redis.lrem(PROCESSING_KEY, 1, raw)
                    continue
                if self.redis.get(self._lease_key(job.job_id)):
                    continue
                pipeline = self.redis.pipeline(transaction=True)
                pipeline.lrem(PROCESSING_KEY, 1, raw)
                pipeline.rpush(QUEUE_KEY, raw)
                pipeline.execute()
                recovered += 1
        finally:
            self.redis.delete(recovery_key)
        return recovered

    def submit(self, job: JMJob) -> None:
        self.submit_many([job])

    def submit_many(self, jobs: list[JMJob]) -> None:
        """Atomically append a batch while preserving its submission order."""
        payloads = [
            json.dumps(job.to_dict(), ensure_ascii=False, separators=(",", ":"))
            for job in jobs
        ]
        if payloads:
            self.redis.lpush(QUEUE_KEY, *payloads)

    def claim(self, *, timeout: int = 5) -> ClaimedJMJob | None:
        raw = self.redis.brpoplpush(QUEUE_KEY, PROCESSING_KEY, timeout=timeout)
        if not raw:
            return None
        try:
            job = JMJob.from_dict(json.loads(raw))
        except Exception:
            self.redis.lrem(PROCESSING_KEY, 1, raw)
            return None
        lease_token = uuid4().hex
        acquired = self.redis.set(
            self._lease_key(job.job_id),
            lease_token,
            nx=True,
            ex=job.lease_seconds,
        )
        if not acquired:
            self.redis.lrem(PROCESSING_KEY, 1, raw)
            return None
        return ClaimedJMJob(job=job, raw=raw, lease_token=lease_token)

    def renew(self, claim: ClaimedJMJob) -> bool:
        key = self._lease_key(claim.job.job_id)
        if hasattr(self.redis, "eval"):
            return bool(
                self.redis.eval(
                    "if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end; return redis.call('EXPIRE', KEYS[1], ARGV[2])",
                    1,
                    key,
                    claim.lease_token,
                    claim.job.lease_seconds,
                )
            )
        if self.redis.get(key) != claim.lease_token:
            return False
        return bool(self.redis.expire(key, claim.job.lease_seconds))

    def complete(self, claim: ClaimedJMJob, result: dict[str, Any]) -> bool:
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        lease_key = self._lease_key(claim.job.job_id)
        result_key = self._result_key(claim.job.job_id)
        if hasattr(self.redis, "eval"):
            return bool(
                self.redis.eval(
                    """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[3], ARGV[2], 'EX', 86400)
redis.call('LREM', KEYS[2], 1, ARGV[3])
redis.call('DEL', KEYS[1])
return 1
""",
                    3,
                    lease_key,
                    PROCESSING_KEY,
                    result_key,
                    claim.lease_token,
                    payload,
                    claim.raw,
                )
            )
        if self.redis.get(lease_key) != claim.lease_token:
            return False
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.set(result_key, payload, ex=86400)
        pipeline.lrem(PROCESSING_KEY, 1, claim.raw)
        pipeline.delete(lease_key)
        pipeline.execute()
        return True

    def result(self, job_id: str) -> dict[str, Any] | None:
        raw = self.redis.get(self._result_key(job_id))
        if not raw:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
