from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from oopzbot.volume import VOLUME_KEY, saved_volume, set_music_volume


class VolumeTests(unittest.TestCase):
    def test_missing_defaults_to_30_and_saved_mute_survives_restart(self):
        state = {}
        redis = SimpleNamespace(get=state.get, set=state.__setitem__)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(saved_volume(redis), 30)
            music = SimpleNamespace(queue=SimpleNamespace(redis=redis),
                                    voice=SimpleNamespace(available=False, set_volume=Mock()))
            self.assertTrue(set_music_volume(music, 0)["ok"])
            # A fresh reader recovers the saved value, including zero.
            self.assertEqual(saved_volume(SimpleNamespace(get=state.get)), 0)
            music.voice.set_volume.assert_not_called()

    def test_live_volume_applied_and_persisted(self):
        redis = Mock()
        voice = Mock(available=True)
        voice.set_volume.return_value = True
        music = SimpleNamespace(queue=SimpleNamespace(redis=redis), voice=voice)
        self.assertTrue(set_music_volume(music, 30)["ok"])
        redis.set.assert_called_once_with(VOLUME_KEY, "30")
        voice.set_volume.assert_called_once_with(30)

    def test_invalid_volume_does_not_write(self):
        music = Mock()
        self.assertFalse(set_music_volume(music, 101)["ok"])
        music.queue.redis.set.assert_not_called()

    def test_live_failure_is_reported_but_retained_for_reconnect(self):
        music = Mock()
        music.voice.set_volume.return_value = False
        result = set_music_volume(music, 30)
        self.assertFalse(result["ok"])
        music.queue.redis.set.assert_called_once_with(VOLUME_KEY, "30")

    def test_saved_value_takes_priority_over_environment_default(self):
        with patch.dict(os.environ, {"OOPZ_DEFAULT_VOLUME": "40"}):
            self.assertEqual(saved_volume(SimpleNamespace(get=lambda _: None)), 40)
            self.assertEqual(saved_volume(SimpleNamespace(get=lambda _: "25")), 25)
