from __future__ import annotations

import unittest
from unittest.mock import patch

from oopzbot.commands.sessions import ExpiringSessionStore


class ExpiringSessionStoreTests(unittest.TestCase):
    def test_capacity_evicts_least_recently_written_session(self) -> None:
        store = ExpiringSessionStore(300, max_entries=2)

        store.put("first", value=1)
        store.put("second", value=2)
        store.put("third", value=3)

        self.assertIsNone(store.get_active("first"))
        self.assertEqual(set(store), {"second", "third"})

    def test_put_and_len_purge_expired_sessions(self) -> None:
        store = ExpiringSessionStore(10, max_entries=3)
        with patch("oopzbot.commands.sessions.time.monotonic", return_value=100):
            store.put("expired", value=1)
        with patch("oopzbot.commands.sessions.time.monotonic", return_value=111):
            self.assertEqual(len(store), 0)
            store.put("active", value=2)
            self.assertEqual(set(store), {"active"})

    def test_expired_session_is_not_available_through_mapping_access(self) -> None:
        store = ExpiringSessionStore(10)
        with patch("oopzbot.commands.sessions.time.monotonic", return_value=100):
            store.put("expired", value=1)

        with patch("oopzbot.commands.sessions.time.monotonic", return_value=111):
            with self.assertRaises(KeyError):
                _ = store["expired"]


if __name__ == "__main__":
    unittest.main()
