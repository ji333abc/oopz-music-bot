from __future__ import annotations

import json
import unittest

from oopzbot.sse import encode_event, panel_event_stream
from oopzbot.state_publisher import StatePublisher


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class SSEEncodingTests(unittest.TestCase):
    def test_encodes_unicode_snapshot_as_one_json_data_line(self) -> None:
        encoded = encode_event(
            "snapshot",
            {"schema_version": 2, "revision": 7, "message": "队列已更新"},
            event_id=7,
        )
        lines = encoded.splitlines()
        self.assertEqual(lines[0], "id: 7")
        self.assertEqual(lines[1], "event: snapshot")
        self.assertEqual(json.loads(lines[2].removeprefix("data: "))["revision"], 7)
        self.assertTrue(encoded.endswith("\n\n"))

    def test_rejects_event_name_injection(self) -> None:
        with self.assertRaises(ValueError):
            encode_event("state\ndata: leaked", {"ok": True})


class PanelEventStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_snapshot_then_state_and_clean_close(self) -> None:
        publisher = StatePublisher()
        publisher.publish()
        stream = panel_event_stream(
            request=_ConnectedRequest(),
            publisher=publisher,
            panel_snapshot=lambda: {"ok": True},
            heartbeat_seconds=1,
        )

        initial = await anext(stream)
        self.assertIn("event: snapshot", initial)
        self.assertIn("id: 1", initial)

        publisher.publish()
        changed = await anext(stream)
        self.assertIn("event: state", changed)
        self.assertIn("id: 2", changed)

        publisher.close()
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_revision_gap_emits_reset_before_snapshot(self) -> None:
        publisher = StatePublisher(history_limit=2)
        for _ in range(4):
            publisher.publish()
        stream = panel_event_stream(
            request=_ConnectedRequest(),
            publisher=publisher,
            panel_snapshot=lambda: {"ok": True},
            after_revision=1,
        )

        reset = await anext(stream)
        snapshot = await anext(stream)

        self.assertIn("event: reset", reset)
        self.assertIn("id: 4", reset)
        self.assertIn("event: snapshot", snapshot)
        self.assertIn('\"state_revision\":4', snapshot)
        await stream.aclose()

    async def test_idle_timeout_emits_heartbeat_without_snapshot_work(self) -> None:
        snapshots = 0

        def snapshot() -> dict:
            nonlocal snapshots
            snapshots += 1
            return {"ok": True}

        class IdlePublisher:
            revision = 7
            oldest_revision = 7

            @staticmethod
            def wait_for_change(*_args):
                return None

        stream = panel_event_stream(
            request=_ConnectedRequest(),
            publisher=IdlePublisher(),  # type: ignore[arg-type]
            panel_snapshot=snapshot,
            heartbeat_seconds=20,
        )

        await anext(stream)
        heartbeat = await anext(stream)

        self.assertIn("event: heartbeat", heartbeat)
        self.assertEqual(snapshots, 1)
        await stream.aclose()


if __name__ == "__main__":
    unittest.main()
