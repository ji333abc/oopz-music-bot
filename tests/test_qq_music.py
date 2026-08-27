import unittest

from oopzbot.music import QQMusic


class QQMusicLyricTests(unittest.TestCase):
    def test_extracts_current_api_response_lyric(self):
        lyric = "[00:01.00]第一句"
        self.assertEqual(
            QQMusic._extract_lyric({"response": {"lyric": lyric}}),
            lyric,
        )

    def test_keeps_legacy_lyric_shapes(self):
        self.assertEqual(
            QQMusic._extract_lyric({"data": {"lyric": "[00:02]旧结构"}}),
            "[00:02]旧结构",
        )
        self.assertEqual(
            QQMusic._extract_lyric({"lyric": "[00:03]顶层结构"}),
            "[00:03]顶层结构",
        )

    def test_rejects_invalid_payloads(self):
        self.assertEqual(QQMusic._extract_lyric({"response": {}}), "")
        self.assertEqual(QQMusic._extract_lyric([]), "")


class QQMusicSearchTests(unittest.TestCase):
    @staticmethod
    def _client_with_response(response):
        client = QQMusic.__new__(QQMusic)
        calls = []

        def fake_get(path, params=None):
            calls.append((path, params))
            return response

        client._get = fake_get
        return client, calls

    def test_search_uses_supported_upstream_route(self):
        client, calls = self._client_with_response(
            {
                "response": {
                    "data": {
                        "song": {
                            "list": [
                                {
                                    "songmid": "song-mid",
                                    "songname": "测试歌曲",
                                    "singer": [{"name": "测试歌手"}],
                                    "album": {"name": "测试专辑", "mid": "album-mid"},
                                    "interval": 180,
                                }
                            ]
                        }
                    }
                }
            }
        )

        song = client.search("测试", limit=10)

        self.assertEqual(song["mid"], "song-mid")
        self.assertEqual(calls, [("/getSearchByKey", {"key": "测试", "limit": 10, "page": 1})])

    def test_empty_search_does_not_call_nonexistent_legacy_route(self):
        client, calls = self._client_with_response(
            {"response": {"data": {"song": {"list": []}}}}
        )

        self.assertIsNone(client.search("不存在的歌曲"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/getSearchByKey")


if __name__ == "__main__":
    unittest.main()
