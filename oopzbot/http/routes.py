"""FastAPI transport routes for the internal command bridge."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from oopzbot.config import get_settings
from oopzbot.domain.compat import command_result_to_legacy
from oopzbot.domain.contracts import CommandRequest
from oopzbot.observability import command_context, ensure_command_id
from oopzbot.sse import panel_event_stream
from oopzbot.state_publisher import StatePublisher

from .security import authorize_bridge_request
from .validation import CommandInputError, command_request_from_http


def create_bridge_router(
    *,
    execute: Callable[[CommandRequest], Any],
    record_command: Callable[[str, dict, str], None],
    panel_snapshot: Callable[[], dict],
    readiness_snapshot: Callable[[], dict],
    music_ready: Callable[[], bool],
    logger: logging.Logger,
    state_publisher: StatePublisher | None = None,
):
    """Build routes around injected application and status boundaries."""

    router = APIRouter()

    @router.post("/internal/qqbot/command")
    async def qqbot_command(request: Request):
        if rejection := authorize_bridge_request(request, get_settings()):
            return rejection

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"ok": False, "message": "请求体不是有效 JSON"},
                status_code=400,
            )

        command_id = ensure_command_id(body.get("command_id"))
        try:
            command_request = command_request_from_http(body, command_id=command_id)
        except CommandInputError as exc:
            return JSONResponse(
                {"ok": False, "message": str(exc)},
                status_code=400,
            )

        try:
            with command_context(command_id):
                result = await asyncio.to_thread(execute, command_request)
                result = command_result_to_legacy(result)
                record_command(
                    command_request.command,
                    result,
                    command_request.requester_name,
                )
                if state_publisher is not None:
                    state_publisher.publish()
            status_code = 409 if result.get("code") == "queue_conflict" else 200
            return JSONResponse(
                {**result, "command_id": command_id}, status_code=status_code
            )
        except Exception as exc:
            with command_context(command_id):
                logger.exception("执行 QQBot 桥接命令失败")
            return JSONResponse(
                {
                    "ok": False,
                    "message": f"执行失败: {type(exc).__name__}",
                    "command_id": command_id,
                },
                status_code=500,
            )

    @router.get("/internal/panel/snapshot")
    async def panel_snapshot_route(request: Request):
        if rejection := authorize_bridge_request(request, get_settings()):
            return rejection
        try:
            return JSONResponse(await asyncio.to_thread(panel_snapshot))
        except Exception as exc:
            logger.exception("生成面板快照失败")
            return JSONResponse(
                {"ok": False, "message": f"读取状态失败: {type(exc).__name__}"},
                status_code=503,
            )

    @router.get("/internal/panel/events")
    async def panel_events_route(request: Request):
        from fastapi.responses import StreamingResponse

        if rejection := authorize_bridge_request(request, get_settings()):
            return rejection
        settings = get_settings()
        if not settings.panel_sse_enabled:
            return JSONResponse(
                {"ok": False, "message": "Panel SSE 已禁用"}, status_code=404
            )

        publisher = state_publisher or StatePublisher()
        try:
            after = max(0, int(request.headers.get("last-event-id") or 0))
        except ValueError:
            after = 0

        return StreamingResponse(
            panel_event_stream(
                request=request,
                publisher=publisher,
                panel_snapshot=panel_snapshot,
                after_revision=after,
                heartbeat_seconds=float(settings.panel_sse_heartbeat_seconds),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @router.get("/healthz")
    async def healthz():
        # Liveness must never call QQ, OOPZ, Redis, or another external service.
        return {"ok": True, "status": "ok", "music_ready": music_ready()}

    @router.get("/readyz")
    async def readyz():
        """Report whether the bot can process complete business commands."""

        result = await asyncio.to_thread(readiness_snapshot)
        return JSONResponse(result, status_code=200 if result["ok"] else 503)

    return (
        router,
        qqbot_command,
        panel_snapshot_route,
        panel_events_route,
        healthz,
        readyz,
    )
