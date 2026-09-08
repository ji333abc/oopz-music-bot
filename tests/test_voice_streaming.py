import base64
import importlib.util
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "legacy_oopzbot/src"))
try:
    spec = importlib.util.spec_from_file_location(
        "voice_streaming_test_module", ROOT / "legacy_oopzbot/src/music/voice_client.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
finally:
    sys.path.pop(0)
VoiceClient = module.VoiceClient


class VoiceStreamingTests(unittest.TestCase):
    def setUp(self):
        self.voice = VoiceClient.__new__(VoiceClient)
        self.voice._stop_event = threading.Event()
        self.voice._fallback_download_stop = threading.Event()
        self.voice._try_play_remote_url = Mock(return_value=(True, 180))
        self.voice._download_audio_with_retry = Mock(return_value=(b"audio", "audio/mpeg"))
        self.voice._run_on_browser = Mock(return_value={"ok": True, "duration": 180})

    def test_successful_url_does_not_download_or_send_local_audio(self):
        self.assertEqual(self.voice._race_remote_and_local("url", "fallback"), (True, 180))
        self.voice._download_audio_with_retry.assert_not_called()
        self.voice._run_on_browser.assert_not_called()

    def test_failed_url_uses_memory_fallback_and_next_song_retries_url(self):
        self.voice._try_play_remote_url.side_effect = [(False, 0), (True, 180)]
        self.assertEqual(self.voice._race_remote_and_local("url", "fallback"), (True, 180))
        self.voice._run_on_browser.assert_called_once_with(
            "agoraPlayLocal", base64.b64encode(b"audio").decode("ascii"), "audio/mpeg"
        )
        self.assertEqual(self.voice._race_remote_and_local("next", "fallback"), (True, 180))
        self.assertEqual(self.voice._download_audio_with_retry.call_count, 1)

    def test_cancelled_download_cannot_start_after_replacement(self):
        previous_stop = threading.Event()
        def download(*args, **kwargs):
            previous_stop.set()
            self.voice._stop_event = threading.Event()
            return b"old audio", "audio/mpeg"
        self.voice._try_play_remote_url.return_value = (False, 0)
        self.voice._download_audio_with_retry.side_effect = download
        self.assertEqual(self.voice._race_remote_and_local(
            "url", "fallback", stop_event=previous_stop
        ), (False, 0))
        self.voice._run_on_browser.assert_not_called()

    def test_preload_does_not_download_next_song(self):
        self.voice._preload_stop = threading.Event()
        self.voice._preload_lock = threading.Lock()
        self.voice._preloaded = {"old": b"audio"}
        self.voice.preload_audio("next")
        self.assertEqual(self.voice._preloaded, {})
        self.voice._download_audio_with_retry.assert_not_called()

    def test_download_closes_connection_on_success_and_memory_limit(self):
        for oversized in (False, True):
            with self.subTest(oversized=oversized):
                session = Mock()
                response = Mock(headers={"Content-Type": "audio/mpeg"})
                response.iter_content.return_value = (
                    iter([b"x" * (1024 * 1024)] * 65) if oversized else iter([b"audio"])
                )
                manager = MagicMock()
                manager.__enter__.return_value = response
                session.get.return_value = manager
                with patch.object(module.http_requests, "Session", return_value=session):
                    if oversized:
                        with self.assertRaisesRegex(RuntimeError, "64 MiB"):
                            VoiceClient._download_audio_with_retry(self.voice, "https://cdn.invalid/audio")
                    else:
                        self.assertEqual(VoiceClient._download_audio_with_retry(
                            self.voice, "https://cdn.invalid/audio"
                        ), (b"audio", "audio/mpeg"))
                manager.__exit__.assert_called_once()
                session.close.assert_called_once()
