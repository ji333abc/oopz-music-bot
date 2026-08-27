import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "legacy_oopzbot"


class LegacyCoreTests(unittest.TestCase):
    def test_full_legacy_source_tree_is_present(self) -> None:
        python_files = list((LEGACY / "src").rglob("*.py"))
        self.assertGreaterEqual(len(python_files), 140)
        for required in (
            "src/app/lifecycle/context_builder.py",
            "src/music/music.py",
            "src/music/voice_client.py",
            "src/oopz/oopz_client.py",
            "src/oopz/oopz_sender.py",
            "src/web/web_player.py",
            "src/web/assets/agora_sdk.js",
        ):
            self.assertTrue((LEGACY / required).is_file(), required)

    def test_bridge_required_music_methods_are_kept(self) -> None:
        tree = ast.parse(
            (LEGACY / "src/music/music.py").read_text(encoding="utf-8")
        )
        music_handler = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MusicHandler"
        )
        methods = {
            node.name
            for node in music_handler.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            {
                "_get_queue",
                "search_candidates",
                "play_song_choice",
                "play_song",
                "play_next",
                "stop_play",
                "enter_voice_channel",
                "_build_song_data_from_platform_data",
                "_commit_song_request",
            }.issubset(methods)
        )

    def test_generated_python_files_are_excluded_from_image_and_git(self) -> None:
        self.assertIn("__pycache__", (ROOT / ".dockerignore").read_text(encoding="utf-8"))
        self.assertIn("*.py[cod]", (ROOT / ".dockerignore").read_text(encoding="utf-8"))
        self.assertIn("__pycache__/", (ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_compatibility_config_contains_no_embedded_private_key(self) -> None:
        private_key_loader = (LEGACY / "private_key.py").read_text(encoding="utf-8")
        compatibility_config = (LEGACY / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("BEGIN PRIVATE KEY", private_key_loader)
        self.assertNotIn("BEGIN PRIVATE KEY", compatibility_config)
        self.assertIn("OOPZ_PRIVATE_KEY_FILE", private_key_loader)
        self.assertIn("OOPZ_AGORA_APP_ID", compatibility_config)


if __name__ == "__main__":
    unittest.main()
