from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from arthur_loop.browser_lock import BrowserLockError, acquire_lock, read_lock, release_lock
from arthur_loop.queue_ledger import read_jsonl


UTC = timezone.utc


class BrowserLockTests(unittest.TestCase):
    def test_acquire_conflicts_until_stale_then_takes_over(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquire_lock(root, "manager-a", ttl_minutes=15, now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

            with self.assertRaises(BrowserLockError):
                acquire_lock(root, "manager-b", now=datetime(2026, 7, 2, 12, 5, tzinfo=UTC))

            lock = acquire_lock(root, "manager-b", now=datetime(2026, 7, 2, 12, 16, tzinfo=UTC))
            self.assertEqual(lock.holder, "manager-b")

            events = read_jsonl(root / "queue/events.jsonl")
            self.assertIn("lock_takeover", [event["event_type"] for event in events])

    def test_release_only_removes_lock_for_holder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquire_lock(root, "manager-a", now=datetime(2026, 7, 2, 12, 0, tzinfo=UTC))

            self.assertFalse(release_lock(root, "manager-b", now=datetime(2026, 7, 2, 12, 1, tzinfo=UTC)))
            self.assertIsNotNone(read_lock(root))

            self.assertTrue(release_lock(root, "manager-a", now=datetime(2026, 7, 2, 12, 2, tzinfo=UTC)))
            self.assertIsNone(read_lock(root))


if __name__ == "__main__":
    unittest.main()
