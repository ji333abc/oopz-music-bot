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

    def test_album_search_uses_smartbox_album_results(self):
        client, calls = self._client_with_response(
            {
                "data": {
                    "album": {
                        "itemlist": [
                            {
                                "mid": "album-mid",
                                "name": "叶惠美",
                                "singer": "周杰伦",
                                "pic": "https://cover.invalid/album.jpg",
                            }
                        ]
                    }
                }
            }
        )

        albums = client.search_albums("叶惠美")

        self.assertEqual(albums[0]["id"], "album-mid")
        self.assertEqual(albums[0]["artists"], "周杰伦")
        self.assertEqual(calls, [("/getSmartbox", {"key": "叶惠美"})])

    def test_album_detail_normalizes_tracks_without_play_urls(self):
        client, calls = self._client_with_response(
            {
                "data": {
                    "mid": "album-mid",
                    "name": "叶惠美",
                    "singername": "周杰伦",
                    "aDate": "2003-07-31",
                    "list": [
                        {
                            "songmid": "track-mid",
                            "songname": "以父之名",
                            "singer": [{"name": "周杰伦"}],
                            "album": {"name": "叶惠美", "mid": "album-mid"},
                            "interval": 342,
                        }
                    ],
                }
            }
        )

        album = client.get_album("album-mid")

        self.assertEqual(album["track_count"], 1)
        self.assertEqual(album["tracks"][0]["mid"], "track-mid")
        self.assertEqual(album["tracks"][0]["album_mid"], "album-mid")
        self.assertNotIn("url", album["tracks"][0])
        self.assertEqual(calls[0][0], "/getAlbumInfo")


class QQMusicPlaybackTests(unittest.TestCase):
    def test_playback_falls_back_to_128_without_obsolete_route(self):
        client = QQMusic.__new__(QQMusic)
        client.quality = "320"
        client.fallback_quality = "128"
        calls = []

        def fake_get(path, params=None):
            calls.append((path, params))
            if params["quality"] == "320":
                return {"data": {"playUrl": {"song-mid": {"url": ""}}}}
            return {
                "data": {
                    "playUrl": {
                        "song-mid": {"url": "https://cdn.example/song.mp3"}
                    }
                }
            }

        client._get = fake_get

        self.assertEqual(
            client.get_song_url("song-mid"),
            "https://cdn.example/song.mp3",
        )
        self.assertEqual(
            [path for path, _ in calls],
            ["/getMusicPlay", "/getMusicPlay"],
        )

    def test_unplayable_song_returns_none_without_404_probe(self):
        client = QQMusic.__new__(QQMusic)
        client.quality = "320"
        client.fallback_quality = "128"
        calls = []

        def fake_get(path, params=None):
            calls.append((path, params))
            return {"data": {"playUrl": {"song-mid": {"url": ""}}}}

        client._get = fake_get

        self.assertIsNone(client.get_song_url("song-mid"))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(path == "/getMusicPlay" for path, _ in calls))


if __name__ == "__main__":
    unittest.main()
