"""Run one JMComic download in an isolated subprocess.

The ZIP password is read from JM_ZIP_PASSWORD so it is not exposed in the
process command line.  This helper intentionally handles exactly one album.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

PDF_QUALITY_PRIMARY = 85
PDF_QUALITY_FALLBACK = 78
DEFAULT_MAX_ARCHIVE_BYTES = 80 * 1024 * 1024


def _build_option(JmOption, image_dir: Path | None = None, *, log: bool = True):
    config = {
        "log": log,
        "download": {
            "cache": True,
            "threading": {
                # Keep resource usage modest on the 1 GB server.
                "image": 4,
                "photo": 1,
            },
        },
        "client": {
            "impl": "api",
            "retry_times": 5,
            "postman": {
                "meta_data": {
                    "proxies": None,
                },
            },
        },
    }
    if image_dir is not None:
        config["dir_rule"] = {
            "base_dir": str(image_dir),
            "rule": "Bd / Pindex",
        }
    return JmOption.construct(config)


def _episode_id(item) -> str:
    if isinstance(item, (list, tuple)) and item:
        return str(item[0] or "").strip()
    if isinstance(item, dict):
        return str(
            item.get("photo_id")
            or item.get("id")
            or item.get("album_id")
            or ""
        ).strip()
    return str(
        getattr(item, "photo_id", None)
        or getattr(item, "id", None)
        or getattr(item, "album_id", None)
        or ""
    ).strip()


def _count_album_pages(client, album, album_id: str) -> int:
    """兼容新版 album.page_count=0，按章节 photo.page_arr 汇总页数。"""
    try:
        direct_count = int(getattr(album, "page_count", 0) or 0)
    except (TypeError, ValueError):
        direct_count = 0
    if direct_count > 0:
        return direct_count

    episodes = getattr(album, "episode_list", None) or []
    photo_ids = list(
        dict.fromkeys(
            photo_id
            for item in episodes
            if (photo_id := _episode_id(item))
        )
    )
    if not photo_ids:
        photo_ids = [str(album_id)]

    total = 0
    for photo_id in photo_ids:
        try:
            photo = client.get_photo_detail(photo_id, fetch_album=False)
        except TypeError:
            # 兼容不支持 fetch_album 参数的旧版客户端。
            photo = client.get_photo_detail(photo_id)

        pages = getattr(photo, "page_arr", None)
        if pages is not None:
            try:
                count = len(pages)
            except TypeError:
                count = 0
        else:
            try:
                count = int(getattr(photo, "page_count", 0) or 0)
            except (TypeError, ValueError):
                count = 0
        total += max(0, count)
    return total


def _inspect_album(album_id: str) -> None:
    from jmcomic import JmOption

    option = _build_option(JmOption, log=False)
    client = option.new_jm_client()
    album = client.get_album_detail(album_id)
    page_count = _count_album_pages(client, album, album_id)
    print(
        "JM_METADATA="
        + json.dumps(
            {"page_count": page_count},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _natural_key(path: Path, root: Path) -> tuple:
    parts: list[tuple[int, int | str]] = []
    for component in path.relative_to(root).parts:
        for token in re.split(r"(\d+)", component.lower()):
            if not token:
                continue
            parts.append((0, int(token)) if token.isdigit() else (1, token))
    return tuple(parts)


def _find_images(image_dir: Path) -> list[Path]:
    supported = {".webp", ".jpg", ".jpeg", ".png", ".bmp"}
    images = [
        item
        for item in image_dir.rglob("*")
        if item.is_file() and item.suffix.lower() in supported
    ]
    return sorted(images, key=lambda item: _natural_key(item, image_dir))


def _save_as_jpeg(source: Path, target: Path, quality: int) -> None:
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = image.convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(
            target,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
        )


def _build_pdf(
    images: list[Path],
    pdf_path: Path,
    page_dir: Path,
    quality: int,
) -> None:
    import img2pdf

    shutil.rmtree(page_dir, ignore_errors=True)
    page_dir.mkdir(parents=True, exist_ok=True)
    jpeg_pages: list[str] = []
    for index, source in enumerate(images, start=1):
        target = page_dir / f"{index:06d}.jpg"
        _save_as_jpeg(source, target, quality)
        jpeg_pages.append(str(target))

    with pdf_path.open("wb") as output:
        img2pdf.convert(jpeg_pages, outputstream=output)


def _write_encrypted_zip(
    archive_path: Path,
    password: str,
    files: list[tuple[Path, str]],
) -> None:
    import pyzipper

    archive_path.unlink(missing_ok=True)
    with pyzipper.AESZipFile(
        archive_path,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        for source, archive_name in files:
            archive.write(source, archive_name)


def _create_output_archive(
    album_id: str,
    image_dir: Path,
    archive_dir: Path,
    password: str,
) -> tuple[Path, str, int | None, str]:
    images = _find_images(image_dir)
    if not images:
        raise RuntimeError("no images were found for PDF generation")

    max_archive_bytes = int(
        os.environ.get("QQBOT_JM_MAX_BYTES") or DEFAULT_MAX_ARCHIVE_BYTES
    )
    # AES ZIP adds little to an already compressed PDF, but reserve 2% for headers.
    pdf_budget = max(1, int(max_archive_bytes * 0.98))
    pdf_path = archive_dir / f"JM{album_id}.pdf"
    page_dir = archive_dir / "pdf-pages"
    conversion_error = ""

    for quality in (PDF_QUALITY_PRIMARY, PDF_QUALITY_FALLBACK):
        try:
            pdf_path.unlink(missing_ok=True)
            _build_pdf(images, pdf_path, page_dir, quality)
            if pdf_path.stat().st_size <= pdf_budget:
                archive_path = archive_dir / f"JM{album_id}.zip"
                _write_encrypted_zip(
                    archive_path,
                    password,
                    [(pdf_path, pdf_path.name)],
                )
                shutil.rmtree(page_dir, ignore_errors=True)
                shutil.rmtree(image_dir, ignore_errors=True)
                pdf_path.unlink(missing_ok=True)
                return archive_path, "pdf", quality, ""
            print(
                f"PDF at quality {quality} is too large: {pdf_path.stat().st_size} bytes"
            )
        except Exception as exc:
            conversion_error = str(exc).replace("\n", " ")[-300:]
            print(f"PDF generation at quality {quality} failed: {conversion_error}")
            break

    # A PDF that still exceeds the upload budget is less useful than the original pages.
    pdf_path.unlink(missing_ok=True)
    shutil.rmtree(page_dir, ignore_errors=True)
    archive_path = archive_dir / f"JM{album_id}.zip"
    original_files = [
        (source, f"JM{album_id}/{source.relative_to(image_dir).as_posix()}")
        for source in images
    ]
    _write_encrypted_zip(archive_path, password, original_files)
    shutil.rmtree(image_dir, ignore_errors=True)
    reason = conversion_error or "PDF 超过上传大小预算"
    return archive_path, "images", None, reason


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect":
        album_id = sys.argv[2].strip()
        if not re.fullmatch(r"\d{1,12}", album_id):
            raise SystemExit("invalid album id")
        _inspect_album(album_id)
        return

    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: jmcomic_worker.py ALBUM_ID JOB_DIR | --inspect ALBUM_ID"
        )

    album_id = sys.argv[1].strip()
    if not re.fullmatch(r"\d{1,12}", album_id):
        raise SystemExit("invalid album id")

    password = os.environ.get("JM_ZIP_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("JM_ZIP_PASSWORD must contain at least 8 characters")

    job_dir = Path(sys.argv[2]).resolve()
    image_dir = job_dir / "images"
    archive_dir = job_dir / "archives"
    image_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    from jmcomic import JmOption, download_album

    option = _build_option(JmOption, image_dir)

    download_started = time.monotonic()
    result = download_album(
        album_id,
        option,
        check_exception=False,
    )
    download_seconds = time.monotonic() - download_started

    downloader = result.downloader
    successful_images = sum(
        len(image_list)
        for photo_dict in downloader.download_success_dict.values()
        for image_list in photo_dict.values()
    )
    failed_images = len(downloader.download_failed_image)
    failed_photos = len(downloader.download_failed_photo)
    if successful_images == 0:
        raise RuntimeError("no images were downloaded successfully")

    processing_started = time.monotonic()
    final_path, output_format, pdf_quality, fallback_reason = _create_output_archive(
        album_id,
        image_dir,
        archive_dir,
        password,
    )
    processing_seconds = time.monotonic() - processing_started

    (job_dir / "result.json").write_text(
        json.dumps(
            {
                "successful_images": successful_images,
                "failed_images": failed_images,
                "failed_photos": failed_photos,
                "page_count": successful_images + failed_images,
                "download_seconds": round(download_seconds, 3),
                "processing_seconds": round(processing_seconds, 3),
                "output_format": output_format,
                "pdf_quality": pdf_quality,
                "fallback_reason": fallback_reason,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"JM_ARCHIVE={final_path}")


if __name__ == "__main__":
    main()
