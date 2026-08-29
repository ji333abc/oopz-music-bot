"""Validation at the internal command HTTP boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from oopzbot.domain.contracts import CommandRequest


class CommandInputError(ValueError):
    pass


def command_request_from_http(
    body: Mapping[str, Any],
    *,
    command_id: str,
) -> CommandRequest:
    command = str(body.get("command") or "").strip()
    if not command:
        raise CommandInputError("命令不能为空")
    if len(command) > 150:
        raise CommandInputError("命令过长")
    requester_id = str(body.get("requester_id") or "anonymous").strip()
    requester_name = str(body.get("requester_name") or requester_id).strip()
    group_openid = str(body.get("group_openid") or "unknown-group").strip()
    return CommandRequest(
        command=command,
        requester_id=requester_id,
        requester_name=requester_name,
        group_openid=group_openid,
        source="http",
        command_id=command_id,
    )
