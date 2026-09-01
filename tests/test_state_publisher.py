from __future__ import annotations

import unittest

from oopzbot.state_publisher import StatePublisher


class StatePublisherTests(unittest.TestCase):
    def test_revision_is_monotonic_and_history_is_bounded(self) -> None:
        publisher = StatePublisher(history_limit=2)

        self.assertEqual([publisher.publish() for _ in range(4)], [1, 2, 3, 4])
        self.assertEqual(publisher.revision, 4)
        self.assertEqual(publisher.oldest_revision, 3)
        self.assertEqual(publisher.wait_for_change(3, 0), 4)
        self.assertIsNone(publisher.wait_for_change(4, 0))

    def test_invalid_capacity_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            StatePublisher(history_limit=0)

    def test_close_wakes_waiters_and_stops_publication(self) -> None:
        publisher = StatePublisher()
        publisher.close()

        self.assertEqual(publisher.wait_for_change(0, 60), -1)
        self.assertEqual(publisher.publish(), 0)


if __name__ == "__main__":
    unittest.main()
