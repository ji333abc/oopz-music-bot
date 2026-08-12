from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from oopzbot.jm_worker import _count_album_pages, _find_images, _save_as_jpeg


class _FakePhoto:
    def __init__(self, pages: int):
        self.page_arr = [f"{index}.webp" for index in range(pages)]


class _FakeClient:
    def __init__(self, pages_by_id: dict[str, int]):
        self.pages_by_id = pages_by_id
        self.requested: list[str] = []

    def get_photo_detail(self, photo_id: str, fetch_album: bool = False):
        self.requested.append(str(photo_id))
        return _FakePhoto(self.pages_by_id[str(photo_id)])


class _FakeAlbum:
    def __init__(self, page_count=0, episode_list=None):
        self.page_count = page_count
        self.episode_list = episode_list or []


class JMComicMetadataTests(unittest.TestCase):
    def test_uses_direct_page_count_when_available(self) -> None:
        client = _FakeClient({})
        album = _FakeAlbum(page_count=25)

        self.assertEqual(_count_album_pages(client, album, "123"), 25)
        self.assertEqual(client.requested, [])

    def test_sums_photo_pages_for_new_album_shape(self) -> None:
        client = _FakeClient({"101": 12, "102": 18})
        album = _FakeAlbum(
            page_count=0,
            episode_list=[("101", "第一话"), ("102", "第二话")],
        )

        self.assertEqual(_count_album_pages(client, album, "999"), 30)
        self.assertEqual(client.requested, ["101", "102"])

    def test_falls_back_to_album_id_without_episode_list(self) -> None:
        client = _FakeClient({"999": 20})
        album = _FakeAlbum(page_count=0)

        self.assertEqual(_count_album_pages(client, album, "999"), 20)


class JMComicPdfHelpersTests(unittest.TestCase):
    def test_images_are_sorted_by_chapter_and_page_number(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in ("10/2.webp", "2/10.webp", "2/1.webp"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"test")

            ordered = [item.relative_to(root).as_posix() for item in _find_images(root)]

            self.assertEqual(ordered, ["2/1.webp", "2/10.webp", "10/2.webp"])

    def test_transparent_webp_is_converted_to_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.webp"
            target = root / "target.jpg"
            Image.new("RGBA", (32, 48), (255, 0, 0, 128)).save(source, "WEBP")

            _save_as_jpeg(source, target, quality=85)

            with Image.open(target) as converted:
                self.assertEqual(converted.format, "JPEG")
                self.assertEqual(converted.mode, "RGB")
                self.assertEqual(converted.size, (32, 48))


if __name__ == "__main__":
    unittest.main()
