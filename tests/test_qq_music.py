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


if __name__ == "__main__":
    unittest.main()
