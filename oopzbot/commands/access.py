"""User access rules shared by chat transports and the command boundary."""

from __future__ import annotations

from oopzbot.config import get_settings
from oopzbot.domain.contracts import CommandRequest

MUSIC_ACCESS_DENIED = "你已被限制使用机器人的音乐功能，请联系管理员。"


def music_user_blocked(source: str, user_id: str) -> bool:
    settings = get_settings()
    if source == "oopz":
        blocked = settings.oopz_music_blocked_user_uids
    elif source in {"qq", "http", "compat"}:
        blocked = settings.qqbot_music_blocked_user_openids
    else:
        return False
    user_id = str(user_id or "").strip()
    # When a list is configured, missing sender identity must not bypass it.
    return bool(blocked) and (user_id in blocked or user_id in {"", "anonymous"})


def music_request_blocked(request: CommandRequest) -> bool:
    # This authenticated bridge is also used by the independently logged-in Panel.
    if (
        request.source == "http"
        and request.group_openid == "server-panel"
        and request.requester_id.startswith("panel-")
    ):
        return False
    return music_user_blocked(request.source, request.requester_id)
