from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from oopzbot.qqmusic_credential import (
    NETWORK_BACKOFF_SECONDS,
    CookieRefreshService,
    CredentialStore,
    compute_refresh_interval,
    current_cookie,
    propagate_cookie,
)


def credential(**overrides: object) -> dict:
    result: dict[str, object] = {
        "musicid": "12345678",
        "musickey": "key-1",
        "musickey_create_time": time.time(),
        "key_expires_in": 48 * 3600,
        "need_refresh_key_in": 24 * 3600,
        "refresh_key": "refresh-secret",
    }
    result.update(overrides)
    return result


class CredentialStoreTests(unittest.TestCase):
    def test_refresh_interval_clamps_and_halves_earliest_window(self) -> None:
        self.assertEqual(compute_refresh_interval(credential(need_refresh_key_in=2 * 3600)), 6 * 3600)
        self.assertEqual(compute_refresh_interval(credential(need_refresh_key_in=72 * 3600)), 24 * 3600)
        self.assertEqual(compute_refresh_interval(credential(need_refresh_key_in=20 * 3600)), 10 * 3600)

    def test_store_round_trip_publishes_sibling_cookie_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "custom-credential.json")
            store.save(credential(), source="test")
            loaded = store.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded[0]["musickey"], "key-1")  # type: ignore[index]
            state = json.loads(store.state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["cookie"], "uin=12345678; qm_keyst=key-1; qqmusic_key=key-1")
            self.assertNotIn("refresh_key", state)

    def test_current_cookie_uses_file_change_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"QQ_MUSIC_COOKIE_STATE_FILE": str(Path(directory) / "cookie.json")}, clear=False
        ):
            path = Path(os.environ["QQ_MUSIC_COOKIE_STATE_FILE"])
            self.assertEqual(current_cookie("manual"), "manual")
            path.write_text('{"cookie":"first"}', encoding="utf-8")
            self.assertEqual(current_cookie("manual"), "first")
            time.sleep(0.02)
            path.write_text('{"cookie":"second"}', encoding="utf-8")
            self.assertEqual(current_cookie("manual"), "second")


class RefreshServiceTests(unittest.TestCase):
    def test_tick_waits_until_due_then_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory) / "credential.json")
            stale = credential(musickey_create_time=time.time() - 25 * 3600)
            store.save(stale, source="test")
            payload = json.loads(store.path.read_text(encoding="utf-8"))
            payload["saved_at"] = time.time() - 25 * 3600
            store.path.write_text(json.dumps(payload), encoding="utf-8")

            class Refresher:
                async def refresh(self, _value):
                    return credential(musickey="new-key")

            service = CookieRefreshService(store=store, refresher=Refresher())
            delay = asyncio.run(service._tick())
            self.assertGreaterEqual(delay, 6 * 3600)
            self.assertEqual(store.load()[0]["musickey"], "new-key")  # type: ignore[index]

    def test_network_error_backoff_sequence(self) -> None:
        service = CookieRefreshService()
        with patch("oopzbot.qqmusic_credential.require_qqmusic_api", side_effect=RuntimeError):
            delays = [
                service._classify_refresh_error(RuntimeError("offline"), 0).next_check_seconds
                for _ in range(4)
            ]
        self.assertEqual(delays, [*NETWORK_BACKOFF_SECONDS, NETWORK_BACKOFF_SECONDS[-1]])


class PropagationTests(unittest.TestCase):
    def test_propagate_uses_endpoint_and_restarts_owned_child_after_failure(self) -> None:
        settings = SimpleNamespace(
            qq_music_cookie="manual",
            qq_music_cookie_api_url="http://qqmusic:3201",
            bridge_token="token",
        )
        managed = SimpleNamespace(enabled=True, process=object(), cookie_api_url="http://127.0.0.1:3201", restart=Mock())
        response = SimpleNamespace(status_code=503)
        with patch("oopzbot.qqmusic_credential.requests.post", return_value=response) as post:
            result = propagate_cookie(settings, managed_service=managed)
        self.assertEqual(result["launcher"], "http_503")
        managed.restart.assert_called_once_with("manual")
        self.assertEqual(post.call_args.kwargs["headers"]["x-qqbot-bridge-token"], "token")


if __name__ == "__main__":
    unittest.main()
