from __future__ import annotations

import types
import unittest
from unittest.mock import patch

import requests

from oopzbot.metrics import MetricsRegistry
from oopzbot.music import QQMusic


class _Response:
    def __init__(self, status: int = 200, payload=None, *, invalid_json: bool = False) -> None:
        self.status_code = status
        self.payload = payload
        self.invalid_json = invalid_json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid json")
        return self.payload


def _client(getter) -> QQMusic:
    client = QQMusic.__new__(QQMusic)
    client.base_url = "https://music.invalid"
    client.cookie = "private-cookie"
    client.quality = "320"
    client.fallback_quality = "128"
    client.last_error = None
    client._session = types.SimpleNamespace(get=getter)
    return client


class QQMusicDiagnosticsTests(unittest.TestCase):
    def test_http_parse_timeout_and_network_categories_are_distinct(self) -> None:
        cases = [
            (_Response(401, {}), "cookie_invalid"),
            (_Response(404, {}), "upstream_http"),
            (_Response(200, invalid_json=True), "parse_error"),
            (requests.Timeout("slow"), "timeout"),
            (requests.ConnectionError("offline"), "network"),
        ]
        for outcome, expected in cases:
            with self.subTest(expected=expected):
                registry = MetricsRegistry()

                def getter(*_args, _outcome=outcome, **_kwargs):
                    if isinstance(_outcome, Exception):
                        raise _outcome
                    return _outcome

                client = _client(getter)
                with patch("oopzbot.music.metrics", registry), patch(
                    "oopzbot.music.current_cookie", return_value="private-cookie"
                ):
                    self.assertIsNone(client._get("/endpoint"))
                    diagnostics = client.diagnostics()

                self.assertEqual(client.last_error["type"], expected)
                self.assertEqual(
                    diagnostics["endpoints"]["qqmusic:/endpoint"]["result_counts"],
                    {expected: 1},
                )
                self.assertNotIn("private-cookie", str(diagnostics))

    def test_playability_fallback_records_one_final_sample(self) -> None:
        registry = MetricsRegistry()
        responses = iter(
            [
                _Response(200, {"data": {"url": ""}}),
                _Response(200, {"data": {"url": "https://cdn.invalid/song"}}),
            ]
        )
        client = _client(lambda *_args, **_kwargs: next(responses))

        with patch("oopzbot.music.metrics", registry), patch(
            "oopzbot.music.current_cookie", return_value=""
        ):
            url = client.get_song_url("song")

        self.assertEqual(url, "https://cdn.invalid/song")
        playability = registry.summaries()["qqmusic:playability"]
        self.assertEqual(playability["count"], 1)
        self.assertEqual(playability["result_counts"], {"playable": 1})
        self.assertNotIn(url, str(playability))

    def test_empty_urls_record_unplayable_once(self) -> None:
        registry = MetricsRegistry()
        client = _client(
            lambda *_args, **_kwargs: _Response(200, {"data": {"url": ""}})
        )

        with patch("oopzbot.music.metrics", registry), patch(
            "oopzbot.music.current_cookie", return_value=""
        ):
            self.assertIsNone(client.get_song_url("song"))

        playability = registry.summaries()["qqmusic:playability"]
        self.assertEqual(playability["count"], 1)
        self.assertEqual(playability["result_counts"], {"unplayable": 1})


if __name__ == "__main__":
    unittest.main()
