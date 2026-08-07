import json
import os
import tempfile
import unittest

from src.run_coordinator import RunCoordinator


class RunCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.temp_dir.name, "active-run.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _coordinator(self, alive=lambda _pid: True):
        return RunCoordinator(state_path=self.state_path, pid_alive=alive)

    def test_acquire_blocks_second_process_until_lease_is_released(self):
        first = self._coordinator()
        second = self._coordinator()

        lease = first.acquire("batch")

        self.assertIsNotNone(lease)
        self.assertIsNone(second.acquire("scheduled"))

        lease.release()

        replacement = second.acquire("scheduled")
        self.assertIsNotNone(replacement)
        replacement.release()

    def test_stop_request_is_visible_only_to_active_scheduled_lease(self):
        coordinator = self._coordinator()
        lease = coordinator.acquire("scheduled")

        self.assertTrue(coordinator.request_stop_for_scheduled())
        self.assertTrue(lease.stop_requested())

        lease.release()

    def test_stale_lock_is_replaced_before_acquiring(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "origin": "scheduled",
                    "pid": 999999,
                    "session_id": "stale-session",
                    "started_at": "2026-07-30T00:00:00",
                },
                handle,
            )

        coordinator = self._coordinator(alive=lambda pid: pid == os.getpid())
        lease = coordinator.acquire("batch")

        self.assertIsNotNone(lease)
        self.assertEqual("batch", coordinator.active_run()["origin"])
        lease.release()


if __name__ == "__main__":
    unittest.main()
