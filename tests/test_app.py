from __future__ import annotations

import unittest

from oopzbot.discovery import discovery_payload


class FakeRuntime:
    def get_joined_areas(self):
        return [{"areaId": "area-1", "name": "测试域"}]

    def get_area_channels(self, area):
        return [{"name": "频道", "channels": [{"id": "voice-1", "type": "VOICE"}]}]


class DiscoveryTests(unittest.TestCase):
    def test_discovers_channels_for_joined_areas(self) -> None:
        payload = discovery_payload(FakeRuntime())
        self.assertEqual(payload["areas"][0]["id"], "area-1")
        self.assertEqual(payload["areas"][0]["groups"][0]["name"], "频道")

    def test_queries_channels_directly_by_area_id(self) -> None:
        payload = discovery_payload(FakeRuntime(), area_id="manual-area")
        self.assertEqual(payload["areas"][0]["id"], "manual-area")
        self.assertEqual(payload["areas"][0]["groups"][0]["channels"][0]["id"], "voice-1")

    def test_areas_only_skips_channel_query(self) -> None:
        class AreasOnlyRuntime(FakeRuntime):
            def get_area_channels(self, area):
                raise AssertionError("channel query should not run")

        payload = discovery_payload(AreasOnlyRuntime(), areas_only=True)
        self.assertEqual(payload["areas"][0]["groups"], [])


if __name__ == "__main__":
    unittest.main()
