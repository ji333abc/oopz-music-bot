"""Anonymous Bilibili video metadata and DASH audio resolution."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import requests

from .media_proxy import USER_AGENT, relay_audio

_API = "https://api.bilibili.com"
_ORDER = (
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
)
_BV = re.compile(r"BV[0-9A-Za-z]{10}", re.IGNORECASE)


class BilibiliError(RuntimeError):
    pass


def parse_video(value: str) -> tuple[str, int]:
    """Accept a BV ID or a standard Bilibili video URL, with optional page."""
    text = str(value or "").strip()
    page = 1
    if text.startswith(("http://", "https://")):
        parsed = urlsplit(text)
        if (
            parsed.hostname not in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
            or parsed.username
            or parsed.password
        ):
            raise ValueError("请提供 B站视频链接或 BV号")
        text = parsed.path.removeprefix("/video/").rstrip("/")
        values = parse_qs(parsed.query, keep_blank_values=True).get("p", ["1"])
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError("分P编号必须是正整数")
        page = int(values[0])
    else:
        parts = text.split("?", 1)
        text = parts[0]
        if len(parts) == 2:
            values = parse_qs(parts[1], keep_blank_values=True).get("p", ["1"])
            if len(values) != 1 or not values[0].isdigit():
                raise ValueError("分P编号必须是正整数")
            page = int(values[0])
    if not _BV.fullmatch(text) or not 1 <= page <= 10000:
        raise ValueError("用法：b站 BV号，或 b站 https://www.bilibili.com/video/BV号?p=2")
    return "BV" + text[2:], page


def video_id(value: str) -> str:
    bvid, page = parse_video(value)
    return bvid if page == 1 else f"{bvid}?p={page}"


def sign_parameters(params: dict, key: str, timestamp: int) -> str:
    values = dict(params, wts=str(timestamp))
    filtered = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in values.items()}
    query = urlencode(sorted(filtered.items()), quote_via=quote)
    return query + "&w_rid=" + hashlib.md5((query + key).encode()).hexdigest()


class BilibiliVideoProvider:
    name = "bilibili"
    display_name = "B站"

    def __init__(self, cookie: str = ""):
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        )
        if cookie:
            from http.cookies import SimpleCookie

            for name, item in SimpleCookie(cookie).items():
                self._session.cookies.set(name, item.value, domain=".bilibili.com", path="/")
        self._lock = threading.RLock()
        self._guest_until = 0.0
        self._key_until = 0.0
        self._key = ""
        self._cooldown_until = 0.0
        self.last_error: dict | None = None
        self.last_song_url_error = ""

    def _api(self, path: str, query: str = "", *, nav: bool = False) -> dict:
        if time.monotonic() < self._cooldown_until:
            raise BilibiliError("B站访问暂受限制，请稍后重试")
        try:
            with self._session.get(
                _API + path + ("?" + query if query else ""),
                timeout=(5, 15),
                allow_redirects=False,
            ) as response:
                if response.status_code == 412:
                    self._cooldown_until = time.monotonic() + 60
                    self._guest_until = self._key_until = 0
                    raise BilibiliError("B站访问受限（HTTP 412），请稍后重试")
                response.raise_for_status()
                result = response.json()
        except requests.Timeout as exc:
            raise BilibiliError("B站接口请求超时，请稍后重试") from exc
        except (requests.RequestException, ValueError) as exc:
            raise BilibiliError("B站接口暂时不可用，请稍后重试") from exc
        code = result.get("code") if isinstance(result, dict) else None
        data = result.get("data") if isinstance(result, dict) else None
        if code == -412 or (isinstance(data, dict) and data.get("v_voucher")):
            self._cooldown_until = time.monotonic() + 60
            self._guest_until = self._key_until = 0
            raise BilibiliError("B站访问受限，请稍后重试")
        if code not in ((0, -101) if nav else (0,)) or not isinstance(data, dict):
            if code in (-404, 62002):
                raise BilibiliError("B站视频不存在或已被删除")
            if code in (-101, -403):
                raise BilibiliError("该 B站视频需要登录或当前账号无权访问")
            raise BilibiliError(f"B站接口返回异常（code={code}）")
        return data

    def _prepare(self):
        now = time.monotonic()
        if now >= self._guest_until:
            guest = self._api("/x/frontend/finger/spi")
            for name, field in (("buvid3", "b_3"), ("buvid4", "b_4")):
                value = guest.get(field)
                if not isinstance(value, str) or not value:
                    raise BilibiliError("B站未返回完整的访客身份，请稍后重试")
                self._session.cookies.set(name, value, domain=".bilibili.com", path="/")
            self._guest_until = now + 3600
        if now >= self._key_until:
            images = self._api("/x/web-interface/nav", nav=True).get("wbi_img") or {}
            raw = "".join(
                str(images.get(k) or "").rsplit("/", 1)[-1].split(".")[0]
                for k in ("img_url", "sub_url")
            )
            if len(raw) != 64:
                raise BilibiliError("B站签名信息无效，请稍后重试")
            self._key = "".join(raw[i] for i in _ORDER[:32])
            self._key_until = now + 3600

    def _signed(self, path: str, params: dict) -> dict:
        self._prepare()
        return self._api(path, sign_parameters(params, self._key, int(time.time())))

    def _view(self, value: str) -> tuple[dict, dict, str]:
        bvid, page = parse_video(value)
        data = self._signed("/x/web-interface/wbi/view", {"bvid": bvid})
        pages = data.get("pages") or []
        if not isinstance(pages, list):
            raise BilibiliError("B站视频分P信息格式异常")
        if pages:
            if page > len(pages):
                raise BilibiliError(f"该视频只有 {len(pages)} 个分P")
            part = pages[page - 1]
        elif page == 1:
            part = data
        else:
            raise BilibiliError("该视频没有所选分P")
        if not isinstance(part, dict) or not part.get("cid"):
            raise BilibiliError("B站视频信息缺少分P标识")
        return data, part, video_id(value)

    def _failure(self, exc):
        self.last_song_url_error = str(exc)
        self.last_error = {"kind": "dependency", "message": str(exc)}

    def get_song_detail(self, value: str) -> dict | None:
        with self._lock:
            self.last_error = None
            self.last_song_url_error = ""
            try:
                data, part, identifier = self._view(value)
                duration = int(part.get("duration") or data.get("duration") or 0)
                title = str(data.get("title") or "未知视频")
                owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
                if len(data.get("pages") or []) > 1:
                    title += " · " + str(part.get("part") or "分P")
                return {
                    "id": identifier,
                    "name": title,
                    "artists": owner.get("name") or "未知UP主",
                    "album": "",
                    "duration": duration * 1000,
                    "durationText": f"{duration // 60}:{duration % 60:02d}",
                    "cover": data.get("pic") or "",
                    "platform": "bilibili",
                    "url": "",
                }
            except (BilibiliError, ValueError, TypeError) as exc:
                self._failure(exc)
                return None

    def get_song_url(self, value: str, **_ignored) -> str | None:
        with self._lock:
            self.last_error = None
            self.last_song_url_error = ""
            try:
                _data, part, _identifier = self._view(value)
                bvid, _page = parse_video(value)
                data = self._signed(
                    "/x/player/wbi/playurl",
                    {
                        "bvid": bvid,
                        "cid": part["cid"],
                        "fnval": 4048,
                        "fnver": 0,
                        "qn": 64,
                        "fourk": 1,
                    },
                )
                tracks = (data.get("dash") or {}).get("audio") or []
                tracks = [
                    item
                    for item in tracks
                    if isinstance(item, dict) and str(item.get("codecs") or "").startswith("mp4a")
                ]
                if not tracks:
                    raise BilibiliError("该视频没有可用的独立 AAC 音轨，可能需要登录或没有访问权限")
                audio = max(tracks, key=lambda item: int(item.get("bandwidth") or 0))
                return relay_audio(str(audio.get("baseUrl") or audio.get("base_url") or ""))
            except (BilibiliError, ValueError, TypeError) as exc:
                self._failure(exc)
                return None

    def search_many(self, keyword: str, limit: int = 10, offset: int = 0) -> list[dict]:
        if offset or limit < 1:
            return []
        song = self.get_song_detail(keyword)
        return [song] if song else []

    def summarize(self, value: str) -> dict:
        song = self.get_song_detail(value)
        return {
            "code": "success" if song else "error",
            "data": song,
            "message": "" if song else self.last_song_url_error,
        }

    summarize_by_id = summarize

    def close(self):
        self._session.close()
