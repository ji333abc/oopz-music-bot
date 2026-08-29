"""Bounded QQ group reply retry and fallback policy."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any


class ReplyErrorKind(StrEnum):
    DEDUPLICATED = "deduplicated"
    TIMED_OUT = "timed_out"
    PASSIVE_UNAVAILABLE = "passive_unavailable"
    PROACTIVE_FORBIDDEN = "proactive_forbidden"
    OTHER = "other"


def _error_code(error: Exception) -> str:
    for attribute in ("code", "err_code", "error_code"):
        value = getattr(error, attribute, None)
        if value is not None:
            return str(value)
    return ""


def classify_reply_error(error: Exception) -> ReplyErrorKind:
    """Classify SDK failures once, including SDKs that expose only text."""

    code = _error_code(error)
    normalized = str(error).replace(" ", "").lower()
    if code == "40054005" or "40054005" in normalized or "消息被去重" in normalized:
        return ReplyErrorKind.DEDUPLICATED
    if code == "40034105" or "40034105" in normalized or "主动消息失败,无权限" in normalized:
        return ReplyErrorKind.PROACTIVE_FORBIDDEN
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ReplyErrorKind.TIMED_OUT
    if "timeout" in type(error).__name__.lower() or any(
        marker in normalized for marker in ("请求超时", "timedout", "timeout", "连接超时")
    ):
        return ReplyErrorKind.TIMED_OUT
    if code == "40034031" or "40034031" in normalized:
        return ReplyErrorKind.PASSIVE_UNAVAILABLE
    if "msgid" in normalized and any(
        marker in normalized
        for marker in ("过期", "失效", "expired", "invalid", "replylimit")
    ):
        return ReplyErrorKind.PASSIVE_UNAVAILABLE
    if any(
        marker in normalized
        for marker in ("消息id已失效", "回复次数已达上限", "超过回复次数")
    ):
        return ReplyErrorKind.PASSIVE_UNAVAILABLE
    return ReplyErrorKind.OTHER


PostMessage = Callable[..., Awaitable[Any]]


class ReplyPolicy:
    """Send at most two passive attempts and one proactive fallback."""

    def __init__(
        self,
        sequence: Callable[[], int],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sequence = sequence
        self._logger = logger or logging.getLogger(__name__)

    def identity(self, message_id: str, *, proactive: bool = False) -> dict[str, Any]:
        identity: dict[str, Any] = {"msg_seq": self._sequence()}
        if not proactive:
            identity["msg_id"] = message_id
        return identity

    async def send(
        self,
        post_message: PostMessage,
        *,
        group_openid: str,
        payload: dict[str, Any],
    ) -> None:
        request = {"group_openid": group_openid, **payload}
        error: Exception | None = None
        try:
            await post_message(**request)
            return
        except Exception as caught:
            error = caught
            kind = classify_reply_error(caught)

        assert error is not None

        if "msg_id" in request and kind in {
            ReplyErrorKind.DEDUPLICATED,
            ReplyErrorKind.TIMED_OUT,
        }:
            self._logger.warning(
                "被动回复失败，换新 msg_seq 重试: group_openid=%s error=%s",
                group_openid,
                str(error).replace("\n", " ")[-300:],
            )
            request["msg_seq"] = self._sequence()
            try:
                await post_message(**request)
                return
            except Exception as retry_error:
                error = retry_error
                kind = classify_reply_error(error)

        if "msg_id" not in request or kind not in {
            ReplyErrorKind.PASSIVE_UNAVAILABLE,
            ReplyErrorKind.TIMED_OUT,
        }:
            raise error

        self._logger.warning(
            "被动回复失败，改为主动群消息发送: group_openid=%s error=%s",
            group_openid,
            str(error).replace("\n", " ")[-300:],
        )
        request.pop("msg_id", None)
        request["msg_seq"] = self._sequence()
        # This is intentionally the final attempt.  In particular, QQ error
        # 40034105 (no proactive permission) is surfaced without a retry loop.
        await post_message(**request)
