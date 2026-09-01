from __future__ import annotations

import json
import unittest

from oopzbot.jm.contracts import JMJob
from oopzbot.jm.queue import HEARTBEAT_KEY, PROCESSING_KEY, QUEUE_KEY, RedisJMQueue


class _Pipeline:
    def __init__(self, redis) -> None:
        self.redis = redis
        self.actions = []

    def set(self, *args, **kwargs):
        self.actions.append(("set", args, kwargs))
        return self

    def lrem(self, *args, **kwargs):
        self.actions.append(("lrem", args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self.actions.append(("delete", args, kwargs))
        return self

    def rpush(self, *args, **kwargs):
        self.actions.append(("rpush", args, kwargs))
        return self

    def execute(self):
        return [getattr(self.redis, name)(*args, **kwargs) for name, args, kwargs in self.actions]


class _Redis:
    def __init__(self) -> None:
        self.values = {}
        self.lists = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)
        return 1

    def expire(self, key, seconds):
        del seconds
        return 1 if key in self.values else 0

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lpush(self, key, *values):
        for value in values:
            self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def brpoplpush(self, source, destination, timeout=0):
        del timeout
        if not self.lists.get(source):
            return None
        value = self.lists[source].pop()
        self.lists.setdefault(destination, []).insert(0, value)
        return value
    def lrem(self, key, count, value):
        del count
        before = len(self.lists.get(key, []))
        self.lists[key] = [item for item in self.lists.get(key, []) if item != value]
        return before - len(self.lists[key])

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        return list(values[start:] if end == -1 else values[start : end + 1])
    def pipeline(self, transaction=True):
        del transaction
        return _Pipeline(self)


class JMQueueTests(unittest.TestCase):
    def job(self) -> JMJob:
        return JMJob(
            job_id="a" * 32,
            album_id="123",
            requester_ref="masked",
            group_openid="group",
            message_id="message",
            password="password123",
            max_archive_bytes=80 * 1024 * 1024,
            timeout_seconds=1200,
        )

    def test_round_trip_lease_and_result(self) -> None:
        redis = _Redis()
        queue = RedisJMQueue(redis)
        queue.heartbeat()
        queue.submit(self.job())
        claim = queue.claim(timeout=0)
        self.assertTrue(queue.available())
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(claim.job.album_id, "123")
        self.assertFalse(redis.lists[QUEUE_KEY])
        self.assertEqual(len(redis.lists[PROCESSING_KEY]), 1)

        queue.complete(claim, {"ok": True, "archive_bytes": 10})

        self.assertEqual(queue.result(claim.job.job_id)["archive_bytes"], 10)
        self.assertFalse(redis.lists[PROCESSING_KEY])
        self.assertIn(HEARTBEAT_KEY, redis.values)

    def test_lease_is_renewed_and_stale_claim_cannot_complete(self) -> None:
        redis = _Redis()
        queue = RedisJMQueue(redis)
        queue.submit(self.job())
        old = queue.claim(timeout=0)
        assert old is not None
        self.assertTrue(queue.renew(old))

        redis.delete(queue._lease_key(old.job.job_id))
        self.assertEqual(queue.recover_stale(), 1)
        newer = queue.claim(timeout=0)
        assert newer is not None

        self.assertFalse(queue.complete(old, {"ok": True, "source": "old"}))
        self.assertIsNone(queue.result(old.job.job_id))
        self.assertTrue(queue.complete(newer, {"ok": True, "source": "new"}))
        self.assertEqual(queue.result(old.job.job_id)["source"], "new")

    def test_invalid_payload_is_discarded(self) -> None:
        redis = _Redis()
        redis.rpush(QUEUE_KEY, json.dumps({"job_id": "bad"}))
        self.assertIsNone(RedisJMQueue(redis).claim(timeout=0))
        self.assertFalse(redis.lists[PROCESSING_KEY])

    def test_expired_lease_is_recovered_once(self) -> None:
        redis = _Redis()
        raw = json.dumps(self.job().to_dict())
        redis.rpush(PROCESSING_KEY, raw)
        queue = RedisJMQueue(redis)

        self.assertEqual(queue.recover_stale(), 1)
        self.assertEqual(redis.lists[QUEUE_KEY], [raw])
        self.assertFalse(redis.lists[PROCESSING_KEY])

    def test_jobs_are_claimed_in_submission_order(self) -> None:
        redis = _Redis()
        queue = RedisJMQueue(redis)
        first = self.job()
        second_data = first.to_dict()
        second_data.update({"job_id": "b" * 32, "album_id": "456"})
        second = JMJob.from_dict(second_data)
        queue.submit_many([first, second])

        first_claim = queue.claim(timeout=0)
        second_claim = queue.claim(timeout=0)

        self.assertEqual(first_claim.job.album_id, "123")
        self.assertEqual(second_claim.job.album_id, "456")


if __name__ == "__main__":
    unittest.main()
