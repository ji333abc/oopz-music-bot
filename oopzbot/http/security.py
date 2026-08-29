"""Internal bridge network and token authorization."""

from __future__ import annotations

import ipaddress
import secrets
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def authorize_bridge_request(request: Request, settings: Any) -> JSONResponse | None:
    client_host = request.client.host if request.client else ""
    private_client = False
    try:
        private_client = ipaddress.ip_address(client_host).is_private
    except ValueError:
        pass
    allowed_client = client_host in _LOOPBACK_HOSTS or (
        settings.bridge_private_network and private_client
    )
    if not allowed_client:
        return JSONResponse(
            {"ok": False, "message": "仅允许本机或已启用的容器内网访问"},
            status_code=403,
        )
    supplied_token = request.headers.get("x-qqbot-bridge-token", "")
    if not settings.bridge_token or not secrets.compare_digest(
        supplied_token,
        settings.bridge_token,
    ):
        return JSONResponse(
            {"ok": False, "message": "桥接认证失败"},
            status_code=401,
        )
    return None
