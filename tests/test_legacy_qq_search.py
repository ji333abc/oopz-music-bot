import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

# Reuse the bridge test's optional FastAPI shim for dependency-light test runs.
from tests.test_bridge_queue import bridge
from oopzbot.application.search_cache import SearchCache

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "legacy_oopzbot/src"))
try:
    spec = importlib.util.spec_from_file_location(
        "legacy_qq_search_test_module", ROOT / "legacy_oopzbot/src/music/qq_music.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
finally:
    sys.path.pop(0)


class LegacySearchTests(unittest.TestCase):
    def client(self):
        client = module.QQMusic.__new__(module.QQMusic)
        client.base_url = "https://music.invalid"
        client.cookie = ""
        client.last_error = None
        client._session = Mock()
        return client

    def test_http_failure_reaches_bridge_and_is_not_negative_cached(self):
        client = self.client()
        response = Mock(status_code=500)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        client._session.get.return_value = response
        cache = SearchCache()
        music = SimpleNamespace(search_candidates=lambda keyword, platform, limit: cache.search(
            platform, keyword, limit=limit,
            loader=lambda: client.search_many(keyword, limit=limit),
        ))
        with patch.object(module, "_current_cookie", return_value=""):
            for _ in range(2):
                result = bridge._search_songs(music, "爱我别走", "test:user")
                self.assertEqual(result["error_kind"], "dependency")
                self.assertIn("HTTP 500", result["message"])
        self.assertEqual(client._session.get.call_count, 2)
        self.assertEqual(cache.snapshot()["size"], 0)
        self.assertTrue(all(call.args[0].endswith("/getSearchByKey") for call in client._session.get.call_args_list))

    def test_valid_empty_response_does_not_probe_obsolete_route(self):
        client = self.client()
        client._get = Mock(return_value={"response": {"code": 0, "data": {"song": {"list": []}}}})
        self.assertEqual(client.search_many("none"), [])
        client._get.assert_called_once()
        client._get.reset_mock()
        self.assertIsNone(client.search("none"))
        client._get.assert_called_once()

    def test_direct_play_search_failure_preserves_original_http_error(self):
        client = self.client()
        client.last_error = {"type": "upstream_http", "message": "QQ音乐接口异常（HTTP 500）"}
        client._get = Mock(return_value=None)
        self.assertIn("HTTP 500", client.summarize("爱我别走")["message"])
        client._get.assert_called_once()

    def test_desktop_response_normalizes_for_existing_bridge(self):
        client = self.client()
        client._get = Mock(return_value={"response": {"code": 0, "data": {"song": {"list": [
            {"mid": "test-mid", "name": "爱我别走", "singer": [{"name": "歌手"}], "interval": 240}
        ]}}}})
        songs = client.search_many("爱我别走")
        self.assertEqual(songs[0]["mid"], "test-mid")
        self.assertEqual(songs[0]["duration"], 240000)
