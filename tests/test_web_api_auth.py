import unittest

from legacy_oopzbot.src.web.web_api_auth import api_request_authorized


class WebApiAuthTests(unittest.TestCase):
    def test_player_cookie_still_authorizes_all_api_paths(self) -> None:
        self.assertTrue(
            api_request_authorized(
                method="POST",
                path="/api/control",
                cookie_token="player-token",
                active_cookie_token="player-token",
                readonly_token="",
                supplied_readonly_token="",
            )
        )

    def test_readonly_token_authorizes_only_allowlisted_paths(self) -> None:
        for path in ("/api/status", "/api/queue", "/api/lyric"):
            with self.subTest(path=path):
                self.assertTrue(
                    api_request_authorized(
                        method="GET",
                        path=path,
                        cookie_token="",
                        active_cookie_token="player-token",
                        readonly_token="readonly-token",
                        supplied_readonly_token="readonly-token",
                    )
                )

        self.assertFalse(
            api_request_authorized(
                method="GET",
                path="/api/control",
                cookie_token="",
                active_cookie_token="player-token",
                readonly_token="readonly-token",
                supplied_readonly_token="readonly-token",
            )
        )

        self.assertFalse(
            api_request_authorized(
                method="POST",
                path="/api/status",
                cookie_token="",
                active_cookie_token="player-token",
                readonly_token="readonly-token",
                supplied_readonly_token="readonly-token",
            )
        )

    def test_empty_or_wrong_readonly_token_is_rejected(self) -> None:
        for expected, supplied in (
            ("", ""),
            ("readonly-token", ""),
            ("readonly-token", "wrong-token"),
        ):
            with self.subTest(expected=expected, supplied=supplied):
                self.assertFalse(
                    api_request_authorized(
                        method="GET",
                        path="/api/status",
                        cookie_token="",
                        active_cookie_token="player-token",
                        readonly_token=expected,
                        supplied_readonly_token=supplied,
                    )
                )


if __name__ == "__main__":
    unittest.main()
