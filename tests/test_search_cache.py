from __future__ import annotations

import threading
import unittest

from oopzbot.application.search_cache import SearchCache, normalize_search_keyword


class _Clock:
    value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SearchCacheTests(unittest.TestCase):
    def test_normalizes_only_outer_and_repeated_whitespace(self) -> None:
        self.assertEqual(normalize_search_keyword("  Hello\t 世界  "), "Hello 世界")

    def test_hit_returns_copy_and_expires(self) -> None:
        clock = _Clock()
        cache = SearchCache(ttl_seconds=60, negative_ttl_seconds=10, max_entries=4, clock=clock)
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            return [{"id": "1", "name": "song"}]

        first = cache.search("qq", " song ", limit=10, loader=load)
        first[0]["name"] = "changed"
        second = cache.search("qq", "song", limit=10, loader=load)
        clock.advance(61)
        third = cache.search("qq", "song", limit=10, loader=load)

        self.assertEqual(second[0]["name"], "song")
        self.assertEqual(third[0]["name"], "song")
        self.assertEqual(calls, 2)
        self.assertEqual(cache.snapshot()["hit"], 1)

    def test_negative_cache_uses_shorter_ttl(self) -> None:
        clock = _Clock()
        cache = SearchCache(ttl_seconds=60, negative_ttl_seconds=10, max_entries=4, clock=clock)
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            return []

        cache.search("qq", "none", limit=10, loader=load)
        cache.search("qq", "none", limit=10, loader=load)
        clock.advance(11)
        cache.search("qq", "none", limit=10, loader=load)

        self.assertEqual(calls, 2)
        self.assertEqual(cache.snapshot()["negative_hit"], 1)

    def test_errors_are_not_cached(self) -> None:
        cache = SearchCache(max_entries=4)
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            raise RuntimeError("offline")

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                cache.search("qq", "song", limit=10, loader=load)
        self.assertEqual(calls, 2)
        self.assertEqual(cache.snapshot()["upstream_error"], 2)

    def test_adapter_reported_error_is_not_negative_cached(self) -> None:
        cache = SearchCache(max_entries=4)
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            return []

        for _ in range(2):
            self.assertEqual(
                cache.search(
                    "qq",
                    "failed",
                    limit=10,
                    loader=load,
                    cacheable=lambda _value: False,
                ),
                [],
            )

        self.assertEqual(calls, 2)
        self.assertEqual(cache.snapshot()["size"], 0)
        self.assertEqual(cache.snapshot()["upstream_error"], 2)

    def test_same_key_concurrency_is_coalesced(self) -> None:
        cache = SearchCache(max_entries=4)
        entered = threading.Event()
        release = threading.Event()
        results: list[list[dict]] = []
        calls = 0

        def load():
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(1)
            return [{"id": "1"}]

        def run():
            results.append(cache.search("qq", "song", limit=10, loader=load))

        first = threading.Thread(target=run)
        second = threading.Thread(target=run)
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        release.set()
        first.join(1)
        second.join(1)

        self.assertEqual(calls, 1)
        self.assertEqual(results, [[{"id": "1"}], [{"id": "1"}]])
        self.assertEqual(cache.snapshot()["coalesced"], 1)

    def test_lru_and_disabled_mode(self) -> None:
        cache = SearchCache(max_entries=2)
        for keyword in ("one", "two", "three"):
            cache.search("qq", keyword, limit=1, loader=lambda keyword=keyword: [{"id": keyword}])
        self.assertEqual(cache.snapshot()["size"], 2)
        self.assertEqual(cache.snapshot()["eviction"], 1)

        disabled = SearchCache(enabled=False)
        value = [{"id": "one"}]
        result = disabled.search("qq", "one", limit=1, loader=lambda: value)
        result[0]["id"] = "changed"
        self.assertEqual(value[0]["id"], "one")
        self.assertEqual(disabled.snapshot()["size"], 0)


if __name__ == "__main__":
    unittest.main()
