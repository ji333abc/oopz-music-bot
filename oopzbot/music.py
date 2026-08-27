"""QQ 音乐平台实现。

服务地址来自 ``QQ_MUSIC_BASE_URL``；默认由主进程自动托管固定版本 API。
"""

from __future__ import annotations

import logging

import requests

from .config import Settings, get_settings

logger = logging.getLogger("QQMusic")
HTTP_TIMEOUT_DEFAULT = 20

# 专辑封面 CDN 模板，{mid} 为专辑 mid。
_ALBUM_COVER_URL = "https://y.gtimg.cn/music/photo_new/T002R300x300M000{mid}.jpg"
_SUPPORTED_QUALITIES = {"m4a", "128", "320", "ape", "flac"}


class QQMusic:
    """QQ 音乐平台，实现 MusicPlatform 协议。"""

    name = "qq"
    display_name = "QQ音乐"

    def __init__(self, settings: Settings | None = None):
        settings = settings or get_settings()
        self.enabled = settings.qq_music_enabled
        self.base_url = settings.qq_music_base_url.rstrip("/")
        self.cookie = settings.qq_music_cookie
        self.quality = self._normalize_quality(
            settings.qq_music_quality,
            default="320",
        )
        self.fallback_quality = self._normalize_quality(
            settings.qq_music_fallback_quality,
            default="128",
        )
        self._session = requests.Session()
        if self.enabled and not self.base_url:
            logger.warning("QQ 音乐 API 地址未配置 (QQ_MUSIC_BASE_URL)")

    @staticmethod
    def _normalize_quality(value, default: str = "128") -> str:
        quality = str(value or "").strip().lower()
        return quality if quality in _SUPPORTED_QUALITIES else default

    def _get(self, path: str, params: dict | None = None) -> dict | None:
        if not self.base_url:
            return None
        try:
            headers = {}
            if self.cookie:
                headers["Cookie"] = self.cookie
            response = self._session.get(
                f"{self.base_url}{path}",
                params=params,
                headers=headers,
                timeout=HTTP_TIMEOUT_DEFAULT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("QQ 音乐 API 请求失败: %s", exc)
            return None

    @staticmethod
    def _extract_songs(data: dict) -> list[dict]:
        """兼容不同版本 QQ Music API 的搜索响应结构。"""
        if not isinstance(data, dict):
            return []

        response = data.get("response") or {}
        response_data = (
            response.get("data") or {} if isinstance(response, dict) else {}
        )
        data_field = data.get("data") or {}

        candidates = [
            (response_data.get("song") or {}).get("list"),
            (response.get("song") or {}).get("list")
            if isinstance(response, dict)
            else None,
            (data_field.get("song") or {}).get("list")
            if isinstance(data_field, dict)
            else None,
            data_field.get("list") if isinstance(data_field, dict) else None,
        ]

        for songs in candidates:
            if isinstance(songs, list):
                return songs
        return []

    def search(self, keyword: str, limit: int = 1) -> dict | None:
        data = self._get(
            "/getSearchByKey",
            params={"key": keyword, "limit": limit, "page": 1},
        )
        songs = self._extract_songs(data or {})

        if not songs:
            return None
        return self._parse_song(songs[0])

    def search_many(
        self,
        keyword: str,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict]:
        page = (offset // max(limit, 1)) + 1
        data = self._get(
            "/getSearchByKey",
            params={"key": keyword, "limit": limit, "page": page},
        )
        songs = self._extract_songs(data or {})

        return [
            parsed
            for song in songs
            if (parsed := self._parse_song(song)) is not None
        ]

    @staticmethod
    def _extract_play_url(data: dict, song_id: str) -> str:
        """从新旧版本播放接口响应中提取播放地址。"""
        if not isinstance(data, dict):
            return ""

        payload = data.get("data") or {}
        if not isinstance(payload, dict):
            return ""

        url = payload.get("url") or ""
        if url:
            return str(url)

        # 新版 QQMusicApi：data.playUrl.<songmid>.url
        play_urls = payload.get("playUrl") or {}
        if isinstance(play_urls, dict):
            item = play_urls.get(song_id)
            if item is None and play_urls:
                item = next(iter(play_urls.values()))
            if isinstance(item, dict):
                url = item.get("url") or ""
            elif isinstance(item, str):
                url = item

        return str(url) if url else ""

    def get_song_url(self, song_id, quality: str | None = None, **_ignored) -> str | None:
        song_id = str(song_id)
        selected_quality = self._normalize_quality(
            quality,
            default=self.quality,
        )
        qualities = [selected_quality]
        if quality is None and self.fallback_quality not in qualities:
            qualities.append(self.fallback_quality)

        # 新接口支持明确指定音质。主音质不可用时，仅默认请求自动降级。
        for candidate_quality in qualities:
            data = self._get(
                "/getMusicPlay",
                params={
                    "songmid": song_id,
                    "quality": candidate_quality,
                },
            )
            url = self._extract_play_url(data or {}, song_id)
            if url:
                return url

        # Both supported QQ Music API services expose /getMusicPlay.  Do not
        # probe the obsolete /song/url route: it returns 404 on current APIs
        # and obscures the real reason when QQ marks a track unplayable.
        return None

    def get_fallback_song_url(self, song_id) -> str | None:
        """获取 Python 本地下载使用的低码率备用链接。"""
        return self.get_song_url(song_id, quality=self.fallback_quality)

    def get_song_detail(self, song_id) -> dict | None:
        data = self._get("/getSongInfo", params={"songmid": str(song_id)})
        if not data or not data.get("data"):
            return None
        return self._parse_song(data["data"])

    def get_lyric(self, song_id) -> str | None:
        data = self._get("/getLyric", params={"songmid": str(song_id)})
        lyric = self._extract_lyric(data or {})
        return lyric if lyric and "[" in lyric else None

    @staticmethod
    def _extract_lyric(data: dict) -> str:
        """兼容 QQ Music API 新旧版本的歌词响应结构。"""
        if not isinstance(data, dict):
            return ""

        for payload in (data.get("response"), data.get("data"), data):
            if not isinstance(payload, dict):
                continue
            lyric = payload.get("lyric") or ""
            if isinstance(lyric, str) and lyric:
                return lyric
        return ""

    def summarize(self, keyword: str) -> dict:
        song = self.search(keyword)
        if not song:
            return {
                "code": "error",
                "message": f"QQ音乐未找到: {keyword}",
                "data": None,
            }
        mid = song.get("mid") or song.get("id")
        url = self.get_song_url(mid)
        if not url:
            return {
                "code": "error",
                "message": f"QQ音乐无法获取播放链接: {song['name']}",
                "data": None,
            }
        song["url"] = url
        message = (
            f"歌曲: {song['name']}\n"
            f"歌手: {song['artists']}\n"
            f"专辑: {song['album']}\n"
            f"时长: {song['durationText']}"
        )
        return {"code": "success", "message": message, "data": song}

    def summarize_by_id(self, song_id) -> dict:
        song = self.get_song_detail(song_id)
        if not song:
            return {
                "code": "error",
                "message": f"QQ音乐无法获取歌曲信息: {song_id}",
                "data": None,
            }
        mid = song.get("mid") or song.get("id")
        url = self.get_song_url(mid)
        if not url:
            return {
                "code": "error",
                "message": f"QQ音乐无法获取播放链接: {song['name']}",
                "data": None,
            }
        song["url"] = url
        return {"code": "success", "message": "", "data": song}

    def _parse_song(self, song: dict) -> dict | None:
        if not song:
            return None

        song_id = song.get("songmid") or song.get("mid") or song.get("id") or ""
        if not song_id:
            return None

        singers = song.get("singer") or []
        if isinstance(singers, list):
            artists = " / ".join(
                str(singer.get("name") or "未知")
                for singer in singers
                if isinstance(singer, dict)
            ) or "未知"
        else:
            artists = str(singers)

        album = song.get("album") or {}
        if isinstance(album, dict):
            album_name = album.get("name") or song.get("albumname") or ""
            album_mid = album.get("mid") or song.get("albummid") or ""
        else:
            album_name = str(album) or str(song.get("albumname") or "")
            album_mid = str(song.get("albummid") or "")

        duration_s = int(song.get("interval") or song.get("duration") or 0)
        cover = (
            _ALBUM_COVER_URL.format(mid=album_mid)
            if album_mid
            else ""
        )

        return {
            "id": song_id,
            "mid": song_id,
            "name": song.get("songname") or song.get("name") or "未知歌曲",
            "artists": artists,
            "album": album_name,
            "duration": duration_s * 1000,
            "durationText": f"{duration_s // 60}:{duration_s % 60:02d}",
            "cover": cover,
        }
