from __future__ import annotations

import json
import random
import threading
import time

import redis
from config import REDIS_CONFIG
from core.logger_config import get_logger
from core.redis_keys import (
    CURRENT as KEY_CURRENT,
)
from core.redis_keys import (
    DEFAULT_CHANNEL as KEY_DEFAULT_CHANNEL,
)
from core.redis_keys import (
    PLAY_MODE as KEY_PLAY_MODE,
)
from core.redis_keys import (
    PLAY_STATE as KEY_PLAY_STATE,
)
from core.redis_keys import (
    QUEUE as KEY_QUEUE,
)
from core.redis_keys import (
    area_key as _area_key,
)

logger = get_logger("QueueManager")

_redis_client = None
_redis_lock = threading.Lock()
# 处于内存降级状态时，每隔该秒数尝试重连一次真实 Redis。
_REDIS_RETRY_INTERVAL = 30.0
_last_redis_retry = 0.0
_claimed_queue_areas: set[str] = set()
_claimed_queue_areas_lock = threading.Lock()


class _InMemoryRedis:
    """
    简易的内存版 Redis，用于 Redis 无法连接时的降级。
    只实现当前项目用到的最小方法集合。
    """

    def __init__(self):
        self._kv: dict[str, object] = {}
        self._lists: dict[str, list] = {}
        self._expires_at: dict[str, float] = {}
        self._condition = threading.Condition()

    # --- 兼容性方法 ---
    def ping(self):
        return True

    def has_transient_queue_state(self) -> bool:
        """Whether switching back would discard a queue or active playback."""
        with self._condition:
            for key in tuple(self._expires_at):
                self._is_expired(key)
            active_values = any(
                value
                for key, value in self._kv.items()
                if key.endswith(":current") or key.endswith(":play_state")
            )
            return any(self._lists.values()) or active_values

    def _get_list(self, key: str) -> list:
        return self._lists.setdefault(key, [])

    def _is_expired(self, key: str) -> bool:
        expires_at = self._expires_at.get(key)
        if expires_at is None:
            return False
        if time.time() < expires_at:
            return False
        self._kv.pop(key, None)
        self._lists.pop(key, None)
        self._expires_at.pop(key, None)
        return True

    # 列表操作
    def rpush(self, key: str, value):
        with self._condition:
            self._get_list(key).append(value)
            self._condition.notify_all()

    def lpush(self, key: str, value):
        with self._condition:
            self._get_list(key).insert(0, value)
            self._condition.notify_all()

    def lrange(self, key: str, start: int, end: int):
        with self._condition:
            lst = self._lists.get(key, [])
            if end == -1:
                end = len(lst) - 1
            return list(lst[start : end + 1]) if lst else []

    def llen(self, key: str) -> int:
        with self._condition:
            return len(self._lists.get(key, []))

    def lpop(self, key: str):
        with self._condition:
            lst = self._lists.get(key, [])
            if not lst:
                return None
            return lst.pop(0)

    def lindex(self, key: str, index: int):
        with self._condition:
            lst = self._lists.get(key, [])
            try:
                return lst[index]
            except IndexError:
                return None

    def lset(self, key: str, index: int, value):
        with self._condition:
            lst = self._get_list(key)
            if index < 0 or index >= len(lst):
                raise IndexError("list index out of range")
            lst[index] = value

    def lrem(self, key: str, count: int, value):
        with self._condition:
            lst = self._lists.get(key, [])
            if not lst:
                return 0
            removed = 0
            if count > 0:
                new = []
                for item in lst:
                    if removed < count and item == value:
                        removed += 1
                        continue
                    new.append(item)
                self._lists[key] = new
            elif count < 0:
                new = []
                for item in reversed(lst):
                    if removed < -count and item == value:
                        removed += 1
                        continue
                    new.append(item)
                self._lists[key] = list(reversed(new))
            else:
                new = [item for item in lst if item != value]
                removed = len(lst) - len(new)
                self._lists[key] = new
            return removed

    def remove_positions(self, key: str, indexes: list[int]) -> list:
        """Atomically remove zero-based indexes and return original values."""
        with self._condition:
            items = list(self._lists.get(key, []))
            normalized = sorted(set(indexes))
            if (
                not normalized
                or normalized[0] < 0
                or normalized[-1] >= len(items)
            ):
                raise IndexError("queue position out of range")
            selected = set(normalized)
            removed = [items[index] for index in normalized]
            self._lists[key] = [
                item for index, item in enumerate(items) if index not in selected
            ]
            return removed

    def queue_append(self, key: str, version_key: str, value) -> int:
        with self._condition:
            self._get_list(key).append(value)
            self._kv[version_key] = int(self._kv.get(version_key, 0) or 0) + 1
            self._condition.notify_all()
            return len(self._lists[key])

    def queue_prepend(self, key: str, version_key: str, value) -> int:
        with self._condition:
            self._get_list(key).insert(0, value)
            self._kv[version_key] = int(self._kv.get(version_key, 0) or 0) + 1
            self._condition.notify_all()
            return len(self._lists[key])

    def queue_pop(self, key: str, version_key: str):
        with self._condition:
            items = self._lists.get(key, [])
            if not items:
                return None
            value = items.pop(0)
            self._kv[version_key] = int(self._kv.get(version_key, 0) or 0) + 1
            return value

    def queue_clear(
        self, key: str, version_key: str, expected_version: int | None = None
    ) -> None:
        with self._condition:
            version = int(self._kv.get(version_key, 0) or 0)
            if expected_version is not None and expected_version != version:
                raise RuntimeError("queue version conflict")
            if self._lists.get(key):
                self._lists.pop(key, None)
                self._kv[version_key] = version + 1

    def remove_positions_versioned(
        self,
        key: str,
        version_key: str,
        indexes: list[int],
        expected_version: int | None = None,
    ) -> list:
        with self._condition:
            version = int(self._kv.get(version_key, 0) or 0)
            if expected_version is not None and expected_version != version:
                raise RuntimeError("queue version conflict")
            items = list(self._lists.get(key, []))
            normalized = sorted(set(indexes))
            if not normalized or normalized[0] < 0 or normalized[-1] >= len(items):
                raise IndexError("queue position out of range")
            removed = [items[index] for index in normalized]
            selected = set(normalized)
            self._lists[key] = [
                item for index, item in enumerate(items) if index not in selected
            ]
            self._kv[version_key] = version + 1
            return removed

    def move_position_versioned(
        self,
        key: str,
        version_key: str,
        source: int,
        target: int,
        expected_version: int | None = None,
    ) -> int:
        with self._condition:
            version = int(self._kv.get(version_key, 0) or 0)
            if expected_version is not None and expected_version != version:
                raise RuntimeError("queue version conflict")
            items = list(self._lists.get(key, []))
            if source < 0 or target < 0 or source >= len(items) or target >= len(items):
                raise IndexError("queue position out of range")
            if source == target:
                return version
            item = items.pop(source)
            items.insert(target, item)
            self._lists[key] = items
            version += 1
            self._kv[version_key] = version
            return version

    def incr(self, key: str) -> int:
        with self._condition:
            value = int(self._kv.get(key, 0) or 0) + 1
            self._kv[key] = value
            return value

    # 字符串 / 通用键
    def set(self, key: str, value, ex: int | None = None, px: int | None = None, **kwargs):
        with self._condition:
            self._kv[key] = value
            if px is not None:
                self._expires_at[key] = time.time() + (float(px) / 1000.0)
            elif ex is not None:
                self._expires_at[key] = time.time() + float(ex)
            else:
                self._expires_at.pop(key, None)

    def get(self, key: str):
        with self._condition:
            if self._is_expired(key):
                return None
            return self._kv.get(key)

    def delete(self, key: str):
        with self._condition:
            self._kv.pop(key, None)
            self._lists.pop(key, None)
            self._expires_at.pop(key, None)

    def blpop(self, key: str, timeout: int = 0):
        """阻塞弹出：使用 Condition 等待，避免 CPU 空转。"""
        deadline = time.monotonic() + max(timeout, 0)
        with self._condition:
            while True:
                lst = self._lists.get(key, [])
                if lst:
                    return key, lst.pop(0)
                if timeout <= 0:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)


class QueueManager:
    """基于 Redis 的播放队列管理器（Redis 不可用时自动回退到内存队列）。
    支持域隔离：传入 area 后 Redis 键自动加域前缀。"""

    def __init__(self, area: str = "", redis_client=None):
        self._pinned_redis = redis_client is not None
        self._redis = redis_client if redis_client is not None else get_redis_client()
        self._area = area
        # Invalidate snapshots held by browsers across a process takeover.
        # Only the first QueueManager for an area advances the generation.
        with _claimed_queue_areas_lock:
            if area not in _claimed_queue_areas:
                try:
                    self._redis.incr(self._vkey())
                except Exception:
                    logger.warning("初始化队列版本失败: area=%s", area or "default")
                _claimed_queue_areas.add(area)

    @property
    def area(self) -> str:
        return self._area

    @property
    def redis(self):
        if self._pinned_redis:
            return self._redis
        # 每次访问都对齐全局客户端：内存降级恢复为真实 Redis 后，
        # 已创建的 QueueManager 实例也能自动切回。
        client = get_redis_client()
        if client is not self._redis:
            self._redis = client
            try:
                client.incr(self._vkey())
            except Exception:
                logger.warning("Redis 接管时推进队列版本失败: area=%s", self._area or "default")
        return self._redis

    def _qkey(self) -> str:
        return _area_key(KEY_QUEUE, self._area)

    def _vkey(self) -> str:
        return _area_key("music:queue_version", self._area)

    def get_version(self) -> int:
        try:
            return max(0, int(self.redis.get(self._vkey()) or 0))
        except (TypeError, ValueError):
            return 0

    def _ckey(self) -> str:
        return _area_key(KEY_CURRENT, self._area)

    def _dkey(self) -> str:
        return _area_key(KEY_DEFAULT_CHANNEL, self._area)

    def _pskey(self) -> str:
        return _area_key(KEY_PLAY_STATE, self._area)

    def _pmkey(self) -> str:
        return _area_key(KEY_PLAY_MODE, self._area)

    # ------------------------------------------------------------------
    # 队列操作
    # ------------------------------------------------------------------

    def add_to_queue(self, song_data: dict) -> int:
        """添加歌曲到队列尾部，返回队列中的位置（0-based）"""
        r = self.redis
        key = self._qkey()
        encoded = json.dumps(song_data, ensure_ascii=False)
        if hasattr(r, "queue_append"):
            pos = int(r.queue_append(key, self._vkey(), encoded)) - 1
        else:
            result = r.eval(
                "redis.call('RPUSH', KEYS[1], ARGV[1]); redis.call('INCR', KEYS[2]); return redis.call('LLEN', KEYS[1])",
                2, key, self._vkey(), encoded,
            )
            pos = int(result) - 1
        logger.info(f"添加到队列: {song_data.get('name')} (位置 {pos})")
        return pos

    def add_to_front(self, song_data: dict) -> int:
        """Return a dequeued song to the front and advance the queue version."""
        r = self.redis
        encoded = json.dumps(song_data, ensure_ascii=False)
        if hasattr(r, "queue_prepend"):
            length = r.queue_prepend(self._qkey(), self._vkey(), encoded)
        else:
            length = r.eval(
                "redis.call('LPUSH', KEYS[1], ARGV[1]); redis.call('INCR', KEYS[2]); return redis.call('LLEN', KEYS[1])",
                2,
                self._qkey(),
                self._vkey(),
                encoded,
            )
        logger.info("歌曲回退到队首: %s", song_data.get("name"))
        return int(length)

    def play_next(self) -> dict | None:
        """从队列头取出下一首"""
        r = self.redis
        if hasattr(r, "queue_pop"):
            data = r.queue_pop(self._qkey(), self._vkey())
        else:
            data = r.eval(
                "local v=redis.call('LPOP', KEYS[1]); if v then redis.call('INCR', KEYS[2]) end; return v",
                2, self._qkey(), self._vkey(),
            )
        if data:
            song = json.loads(data)
            logger.info(f"队列弹出: {song.get('name')}")
            return song
        return None

    def peek_next(self) -> dict | None:
        """查看队首下一首（不弹出），用于预加载"""
        data = self.redis.lindex(self._qkey(), 0)
        if data:
            return json.loads(data)
        return None

    def get_queue(self, start: int = 0, end: int = -1) -> list:
        """获取队列列表"""
        items = self.redis.lrange(self._qkey(), start, end)
        return [json.loads(item) for item in items]

    def get_queue_length(self) -> int:
        return self.redis.llen(self._qkey())

    def clear_queue(self, expected_version: int | None = None):
        """清空队列"""
        r = self.redis
        if hasattr(r, "queue_clear"):
            r.queue_clear(self._qkey(), self._vkey(), expected_version)
        else:
            script = """
local version = tonumber(redis.call('GET', KEYS[2]) or '0')
local expected = tonumber(ARGV[1])
if expected >= 0 and expected ~= version then
  return redis.error_reply('QUEUE_VERSION_CONFLICT')
end
if redis.call('LLEN', KEYS[1]) > 0 then
  redis.call('DEL', KEYS[1])
  redis.call('INCR', KEYS[2])
end
return 1
"""
            try:
                expected = -1 if expected_version is None else int(expected_version)
                r.eval(script, 2, self._qkey(), self._vkey(), expected)
            except Exception as exc:
                if "QUEUE_VERSION_CONFLICT" in str(exc):
                    raise RuntimeError("queue version conflict") from exc
                raise
        logger.info("队列已清空")

    def remove_from_queue(self, index: int) -> bool:
        """移除队列中指定位置的歌曲"""
        try:
            self.remove_positions([int(index) + 1])
            logger.info(f"移除队列位置 {index}")
            return True
        except Exception as e:
            logger.warning("移除队列位置 %d 失败: %s", index, e)
            return False

    def remove_positions(
        self, positions: list[int], expected_version: int | None = None
    ) -> list[dict]:
        """Atomically remove unique one-based pending positions."""
        normalized = sorted(set(int(position) for position in positions))
        if not normalized or normalized[0] < 1:
            raise IndexError("queue position out of range")
        indexes = [position - 1 for position in normalized]
        r = self.redis
        key = self._qkey()
        if hasattr(r, "remove_positions_versioned"):
            values = r.remove_positions_versioned(
                key, self._vkey(), indexes, expected_version
            )
        else:
            placeholder = f"__OOPZ_REMOVED__:{time.time_ns()}:{random.randrange(1 << 30)}"
            script = """
local key = KEYS[1]
local marker = ARGV[1]
local expected = tonumber(ARGV[2])
local version = tonumber(redis.call('GET', KEYS[2]) or '0')
if expected >= 0 and expected ~= version then
  return redis.error_reply('QUEUE_VERSION_CONFLICT')
end
local length = redis.call('LLEN', key)
local removed = {}
for i = 3, #ARGV do
  local index = tonumber(ARGV[i])
  if index < 0 or index >= length then
    return redis.error_reply('QUEUE_POSITION_OUT_OF_RANGE')
  end
  removed[#removed + 1] = redis.call('LINDEX', key, index)
end
for i = #ARGV, 3, -1 do
  redis.call('LSET', key, tonumber(ARGV[i]), marker)
  redis.call('LREM', key, 1, marker)
end
redis.call('INCR', KEYS[2])
return removed
"""
            try:
                expected = -1 if expected_version is None else int(expected_version)
                values = r.eval(
                    script, 2, key, self._vkey(), placeholder, expected, *indexes
                )
            except Exception as exc:
                if "QUEUE_VERSION_CONFLICT" in str(exc):
                    raise RuntimeError("queue version conflict") from exc
                if "QUEUE_POSITION_OUT_OF_RANGE" in str(exc):
                    raise IndexError("queue position out of range") from exc
                raise
        removed = [json.loads(value) for value in values]
        logger.info("批量移除队列位置: %s", normalized)
        return removed

    def move_position(
        self, source: int, target: int, expected_version: int | None = None
    ) -> int:
        """Atomically move one-based source to its final one-based target."""
        source_index = int(source) - 1
        target_index = int(target) - 1
        r = self.redis
        key = self._qkey()
        if hasattr(r, "move_position_versioned"):
            return r.move_position_versioned(
                key, self._vkey(), source_index, target_index, expected_version
            )
        script = """
local length = redis.call('LLEN', KEYS[1])
local source = tonumber(ARGV[1])
local target = tonumber(ARGV[2])
local expected = tonumber(ARGV[3])
local version = tonumber(redis.call('GET', KEYS[2]) or '0')
if expected >= 0 and expected ~= version then
  return redis.error_reply('QUEUE_VERSION_CONFLICT')
end
if source < 0 or target < 0 or source >= length or target >= length then
  return redis.error_reply('QUEUE_POSITION_OUT_OF_RANGE')
end
if source == target then return version end
local items = redis.call('LRANGE', KEYS[1], 0, -1)
local moved = table.remove(items, source + 1)
table.insert(items, target + 1, moved)
redis.call('DEL', KEYS[1])
if #items > 0 then redis.call('RPUSH', KEYS[1], unpack(items)) end
return redis.call('INCR', KEYS[2])
"""
        try:
            expected = -1 if expected_version is None else int(expected_version)
            return int(
                r.eval(
                    script,
                    2,
                    key,
                    self._vkey(),
                    source_index,
                    target_index,
                    expected,
                )
            )
        except Exception as exc:
            if "QUEUE_VERSION_CONFLICT" in str(exc):
                raise RuntimeError("queue version conflict") from exc
            if "QUEUE_POSITION_OUT_OF_RANGE" in str(exc):
                raise IndexError("queue position out of range") from exc
            raise

    def pop_random(self) -> dict | None:
        """随机弹出队列中的一首（用于随机播放模式）。

        Redis LIST 没有原生随机弹出，这里用 LRANGE + LSET/LREM 的占位符模式
        与 remove_from_queue 一致，避免破坏其他索引。
        """
        length = self.get_queue_length()
        if not length:
            return None
        idx = random.randrange(length)
        try:
            song = self.remove_positions([idx + 1])[0]
            logger.info(f"队列随机弹出 (位置 {idx}): {song.get('name')}")
            return song
        except Exception as e:
            logger.warning("随机弹出队列失败: %s", e)
            return None

    # ------------------------------------------------------------------
    # 当前播放
    # ------------------------------------------------------------------

    def set_current(self, song_data: dict):
        """设置当前播放歌曲"""
        self.redis.set(self._ckey(), json.dumps(song_data, ensure_ascii=False))

    def get_current(self) -> dict | None:
        """获取当前播放歌曲"""
        data = self.redis.get(self._ckey())
        if data:
            return json.loads(data)
        return None

    def clear_current(self):
        """清除当前播放"""
        self.redis.delete(self._ckey())

    # ------------------------------------------------------------------
    # 默认频道
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 播放状态（域隔离）
    # ------------------------------------------------------------------

    def set_play_state(self, state: dict):
        self.redis.set(self._pskey(), json.dumps(state))

    def get_play_state(self) -> dict | None:
        raw = self.redis.get(self._pskey())
        return json.loads(raw) if raw else None

    def clear_play_state(self):
        self.redis.delete(self._pskey())

    # ------------------------------------------------------------------
    # 播放模式（域隔离）
    # ------------------------------------------------------------------

    def get_play_mode(self) -> str | None:
        val = self.redis.get(self._pmkey())
        if isinstance(val, bytes):
            val = val.decode("utf-8", errors="ignore")
        return val or None

    def set_play_mode(self, mode: str) -> None:
        self.redis.set(self._pmkey(), mode)

    # ------------------------------------------------------------------
    # 默认频道
    # ------------------------------------------------------------------

    def set_default_channel(self, channel: str):
        self.redis.set(self._dkey(), channel)

    def get_default_channel(self) -> str | None:
        val = self.redis.get(self._dkey())
        if isinstance(val, bytes):
            return val.decode("utf-8", errors="ignore")
        return val


def _try_connect_redis():
    """尝试建立真实 Redis 连接，失败返回 None。"""
    try:
        connection_config = dict(REDIS_CONFIG)
        connection_config.setdefault("socket_connect_timeout", 2.0)
        # The legacy web-command worker uses BLPOP(timeout=2). Keep the socket
        # timeout above that blocking interval so a healthy empty queue is not
        # mistaken for a broken Redis connection.
        connection_config.setdefault("socket_timeout", 5.0)
        client = redis.Redis(**connection_config)
        client.ping()
        return client
    except Exception as e:
        logger.debug(f"Redis 连接尝试失败: {e}")
        return None


def get_redis_client(force_reset: bool = False):
    """返回全局共享 Redis 客户端；连接失败时统一回退到内存实现。

    内存降级不是永久的：之后每隔 _REDIS_RETRY_INTERVAL 秒在访问时探测一次
    真实 Redis。只有内存降级状态为空时才自动切回；存在临时队列或播放
    状态时继续保持 degraded，避免静默丢弃或覆盖用户队列。
    """
    global _redis_client, _last_redis_retry
    with _redis_lock:
        if force_reset:
            _redis_client = None

        if _redis_client is not None and not isinstance(_redis_client, _InMemoryRedis):
            try:
                _redis_client.ping()
            except Exception as exc:
                logger.error("Redis 运行中断开，切换到内存队列: %s", exc)
                _redis_client = _InMemoryRedis()
                _last_redis_retry = time.time()

        if isinstance(_redis_client, _InMemoryRedis):
            now = time.time()
            if now - _last_redis_retry >= _REDIS_RETRY_INTERVAL:
                _last_redis_retry = now
                client = _try_connect_redis()
                if client is not None:
                    if _redis_client.has_transient_queue_state():
                        logger.error(
                            "Redis 已恢复，但内存降级状态非空；为避免队列丢失，"
                            "保持内存模式，待队列清空后再切回"
                        )
                    else:
                        logger.info("Redis 已恢复，从空内存队列切回 Redis")
                        _redis_client = client

        if _redis_client is None:
            _last_redis_retry = time.time()
            client = _try_connect_redis()
            if client is not None:
                logger.info("Redis 连接成功")
                _redis_client = client
            else:
                logger.error("Redis 连接失败，将使用内存队列（每 %.0fs 自动重试）", _REDIS_RETRY_INTERVAL)
                _redis_client = _InMemoryRedis()

        return _redis_client
