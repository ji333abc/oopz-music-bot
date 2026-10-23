from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import requests

from oopzbot.bilibili import BilibiliVideoProvider, parse_video, sign_parameters, video_id
from oopzbot.commands.oopz import normalize_oopz_music_command
from oopzbot.commands.parser import matches_play_command, parse_platform_keyword, play_keyword
from oopzbot.media_proxy import AudioStreamProxy, validate_audio_url

BV = "BV13x41117TL"
CDN = "https://cn-test.bilivideo.com/audio.m4s?deadline=123&secret=not-for-clients"
RAW = "7cd084941338484aae1ad9425b84077c4932caff0ff746eab6f01bf08b70ac45"


def response(payload=None, *, status=200, headers=None, body=b"audio"):
    result = MagicMock()
    result.status_code = status
    result.headers = headers or {}
    result.json.return_value = payload
    result.__enter__.return_value = result

    def close(*_args):
        result.close()
        return False

    result.__exit__.side_effect = close
    result.iter_content.side_effect = lambda _size: iter([body])
    return result


class BilibiliInputTests(unittest.TestCase):
    def test_bv_and_standard_urls_have_stable_page_identity(self):
        self.assertEqual(parse_video(BV), (BV, 1))
        self.assertEqual(
            video_id(f"https://www.bilibili.com/video/{BV}/?p=2&share_source=copy"), BV + "?p=2"
        )
        self.assertEqual(video_id("bv" + BV[2:]), BV)

    def test_rejects_malformed_ids_untrusted_hosts_and_invalid_pages(self):
        for value in (
            BV + "extra",
            BV[:-1],
            BV + "?p=0",
            BV + "?p=-1",
            BV + "?p=abc",
            BV + "?p=1&p=2",
            f"https://bilibili.com.evil/video/{BV}",
            f"https://www.bilibili.com/other/{BV}",
            f"https://user@www.bilibili.com/video/{BV}",
            "http://127.0.0.1/",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_video(value)

    def test_bilibili_command_uses_shared_playback_parser_for_all_transports(self):
        for command in (f"b站 {BV}", f"B站 {BV}", f"bili {BV}", f"bilibili: {BV}"):
            self.assertTrue(matches_play_command(command))
            self.assertEqual(parse_platform_keyword(play_keyword(command)), ("bilibili", BV))
            self.assertEqual(normalize_oopz_music_command(command), command)
        self.assertFalse(matches_play_command("bilibili-not-a-command"))

    def test_wbi_signature_matches_known_vector(self):
        self.assertEqual(
            sign_parameters(
                {"foo": "114", "bar": "514", "zab": "1919810"},
                "ea1db124af3c7062474693fa704f4ff8",
                1702204169,
            ),
            "bar=514&foo=114&wts=1702204169&zab=1919810&w_rid=8f6f2b5b3d485fe1886cec6a0be8c5d4",
        )


class BilibiliProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = BilibiliVideoProvider()
        self.addCleanup(self.provider.close)
        self.calls = []
        self.payloads = {
            "/x/frontend/finger/spi": {"b_3": "visitor3", "b_4": "visitor4"},
            "/x/web-interface/nav": {
                "wbi_img": {
                    "img_url": f"https://i0.hdslb.com/{RAW[:32]}.png",
                    "sub_url": f"https://i0.hdslb.com/{RAW[32:]}.png",
                }
            },
            "/x/web-interface/wbi/view": {
                "title": "视频",
                "cid": 11,
                "duration": 100,
                "owner": {"name": "UP主"},
                "pages": [
                    {"cid": 11, "duration": 40, "part": "第一P"},
                    {"cid": 22, "duration": 60, "part": "第二P"},
                ],
            },
            "/x/player/wbi/playurl": {
                "dash": {
                    "audio": [
                        {"bandwidth": 64000, "codecs": "mp4a.40.2", "baseUrl": CDN},
                        {
                            "bandwidth": 999999,
                            "codecs": "fLaC",
                            "baseUrl": "https://cn-test.bilivideo.com/flac",
                        },
                    ]
                }
            },
        }

        def get(url, **kwargs):
            self.calls.append((url, kwargs))
            path = urlsplit(url).path
            return response(
                {"code": -101 if path.endswith("/nav") else 0, "data": self.payloads[path]}
            )

        self.provider._session.get = MagicMock(side_effect=get)

    def test_anonymous_metadata_uses_spi_and_wbi_and_preserves_page_duration(self):
        song = self.provider.get_song_detail(BV + "?p=2")
        self.assertEqual(song["id"], BV + "?p=2")
        self.assertEqual(song["duration"], 60000)
        self.assertIn("第二P", song["name"])
        self.assertEqual(song["url"], "")
        self.assertNotIn("SESSDATA", self.provider._session.cookies.get_dict())
        self.assertEqual(self.provider._session.cookies.get_dict()["buvid4"], "visitor4")
        self.assertTrue(parse_qs(urlsplit(self.calls[-1][0]).query).get("w_rid"))

    def test_play_url_is_fresh_and_relayed_and_uses_selected_page(self):
        with patch(
            "oopzbot.bilibili.relay_audio", return_value="http://127.0.0.1:1234/opaque"
        ) as relay:
            first = self.provider.get_song_url(BV + "?p=2")
            second = self.provider.get_song_url(BV + "?p=2")
        self.assertEqual(first, second)
        self.assertEqual(relay.call_args.args, (CDN,))
        play_calls = [url for url, _kwargs in self.calls if "/playurl?" in url]
        self.assertEqual(len(play_calls), 2)
        self.assertEqual(parse_qs(urlsplit(play_calls[0]).query)["cid"], ["22"])
        self.assertEqual(sum("/finger/spi" in url for url, _ in self.calls), 1)

    def test_invalid_page_does_not_fetch_playurl(self):
        self.assertIsNone(self.provider.get_song_url(BV + "?p=3"))
        self.assertIn("只有 2", self.provider.last_song_url_error)
        self.assertFalse(any("/playurl" in url for url, _ in self.calls))

    def test_412_stops_and_cools_down_without_retry_or_negative_success(self):
        self.provider._session.get.return_value = response(status=412)
        self.provider._session.get.side_effect = None
        self.assertIsNone(self.provider.get_song_detail(BV))
        self.assertIsNone(self.provider.get_song_detail(BV))
        self.assertEqual(self.provider._session.get.call_count, 1)
        self.assertIsNotNone(self.provider.last_error)

    def test_timeout_error_does_not_expose_signed_url_or_cookie(self):
        self.provider._session.get.side_effect = requests.Timeout("SESSDATA=secret; " + CDN)
        self.assertIsNone(self.provider.get_song_url(BV))
        self.assertIn("超时", self.provider.last_song_url_error)
        self.assertNotIn("secret", self.provider.last_song_url_error)

    def test_http_200_with_platform_failure_is_not_success(self):
        self.provider._session.get.side_effect = None
        self.provider._session.get.return_value = response({"code": -403, "data": None})
        self.assertIsNone(self.provider.get_song_detail(BV))
        self.assertIn("无权访问", self.provider.last_song_url_error)

    def test_missing_audio_does_not_fall_back_to_whole_video(self):
        self.payloads["/x/player/wbi/playurl"] = {"durl": [{"url": CDN}]}
        self.assertIsNone(self.provider.get_song_url(BV))
        self.assertIn("独立 AAC", self.provider.last_song_url_error)


class AudioRelayTests(unittest.TestCase):
    def proxy(self, **kwargs):
        proxy = AudioStreamProxy(**kwargs)
        self.addCleanup(proxy.close)
        return proxy

    def test_get_head_and_range_preserve_media_and_never_forward_credentials(self):
        upstream = response(
            status=206,
            headers={
                "Content-Type": "audio/mp4",
                "Content-Length": "5",
                "Content-Range": "bytes 0-4/50",
                "Accept-Ranges": "bytes",
            },
        )
        getter = MagicMock(return_value=upstream)
        url = self.proxy(getter=getter).register(CDN)
        with requests.get(
            url,
            headers={
                "Range": "bytes=0-4",
                "Cookie": "SESSDATA=private",
                "Authorization": "Bearer private",
            },
            timeout=3,
        ) as result:
            self.assertEqual(result.status_code, 206)
            self.assertEqual(result.content, b"audio")
            self.assertEqual(result.headers["Content-Range"], "bytes 0-4/50")
        headers = getter.call_args.kwargs["headers"]
        self.assertEqual(headers["Referer"], "https://www.bilibili.com/")
        self.assertEqual(headers["Range"], "bytes=0-4")
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("Authorization", headers)
        with requests.head(url, timeout=3) as result:
            self.assertEqual(result.status_code, 206)
            self.assertEqual(result.content, b"")
        self.assertGreaterEqual(upstream.close.call_count, 2)

    def test_rejects_arbitrary_url_and_redirect_to_private_host(self):
        self.assertEqual(
            validate_audio_url("https://upos-hz-mirrorakam.akamaized.net/audio.m4s"),
            "https://upos-hz-mirrorakam.akamaized.net/audio.m4s",
        )
        for url in (
            "http://127.0.0.1/",
            "https://bilivideo.com.evil/audio",
            "https://user@x.bilivideo.com/audio",
            "https://x.bilivideo.com:444/audio",
            "https://unrelated.akamaized.net/audio",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_audio_url(url)
        redirect = response(status=302, headers={"Location": "http://127.0.0.1/private"})
        getter = MagicMock(return_value=redirect)
        url = self.proxy(getter=getter).register(CDN)
        with requests.get(url, timeout=3) as result:
            self.assertEqual(result.status_code, 502)
        self.assertEqual(getter.call_count, 1)
        redirect.close.assert_called_once()

    def test_unknown_expired_and_evicted_tokens_do_not_reach_upstream(self):
        getter = MagicMock()
        proxy = self.proxy(capacity=1, getter=getter)
        old = proxy.register(CDN)
        fresh = proxy.register(CDN)
        for url in (old, fresh + "extra", self.proxy(ttl=-1, getter=getter).register(CDN)):
            with requests.get(url, timeout=3) as result:
                self.assertEqual(result.status_code, 404)
        getter.assert_not_called()

    def test_invalid_range_and_excess_connections_are_rejected(self):
        getter = MagicMock()
        proxy = self.proxy(getter=getter)
        url = proxy.register(CDN)
        for value in ("bytes=-", "bytes=4-2", "bytes=0-1,3-4"):
            with requests.get(url, headers={"Range": value}, timeout=3) as result:
                self.assertEqual(result.status_code, 416)
        for _ in range(4):
            proxy._slots.acquire()
        try:
            with requests.get(url, timeout=3) as result:
                self.assertEqual(result.status_code, 503)
        finally:
            for _ in range(4):
                proxy._slots.release()
        getter.assert_not_called()
