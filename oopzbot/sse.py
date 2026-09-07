"""Small framework-independent Server-Sent Events encoder."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from .metrics import utc_now
from .state_publisher import StatePublisher


def encode_event(
    event: str,
    data: Mapping[str, Any],
    *,
    event_id: int | None = None,
) -> str:
    name = str(event or "")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name):
        raise ValueError("invalid SSE event name")
    lines = []
    if event_id is not None:
        lines.append(f"id: {max(0, int(event_id))}")
    lines.append(f"event: {name}")
    lines.append(
        "data: "
        + json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"))
    )
    return "\n".join(lines) + "\n\n"


async def panel_event_stream(
    *,
    request: Any,
    publisher: StatePublisher,
    panel_snapshot: Callable[[], dict],
    after_revision: int = 0,
    heartbeat_seconds: float = 20,
) -> AsyncIterator[str]:
    """Yield the authenticated Panel protocol without depending on FastAPI."""

    after = max(0, int(after_revision))
    revision = max(after, publisher.revision)
    if after > publisher.revision or (
        after and after < publisher.oldest_revision - 1
    ):
        yield encode_event(
            "reset",
            {
                "schema_version": 2,
                "revision": publisher.revision,
                "generated_at": utc_now(),
            },
            event_id=publisher.revision,
        )
        revision = publisher.revision

    initial = await asyncio.to_thread(panel_snapshot)
    initial.setdefault("schema_version", 2)
    initial["state_revision"] = revision
    initial["generated_at"] = utc_now()
    yield encode_event("snapshot", initial, event_id=revision)

    while not await request.is_disconnected():
        wait_async = getattr(publisher, "wait_for_change_async", None)
        if callable(wait_async):
            changed = await wait_async(revision, float(heartbeat_seconds))
        else:
            # Compatibility for injected publishers that predate the async API.
            changed = await asyncio.to_thread(
                publisher.wait_for_change,
                revision,
                float(heartbeat_seconds),
            )
        if changed == -1:
            break
        if changed is None:
            yield encode_event(
                "heartbeat",
                {
                    "schema_version": 2,
                    "revision": revision,
                    "generated_at": utc_now(),
                },
            )
            continue
        revision = changed
        yield encode_event(
            "state",
            {
                "schema_version": 2,
                "revision": revision,
                "generated_at": utc_now(),
            },
            event_id=revision,
        )
