import threading
import unittest

from src.api import AutoRewarderAPI


class _Lease:
    def __init__(self):
        self.released = False

    def release(self):
        self.released = True

    def stop_requested(self):
        return False


class _Coordinator:
    def __init__(self):
        self.lease = _Lease()
        self.origins = []

    def acquire(self, origin):
        self.origins.append(origin)
        return self.lease

    def active_run(self):
        return None


class _ScheduledRunCoordinator(_Coordinator):
    def __init__(self):
        super().__init__()
        self.stop_requested = False
        self.waited_for_release = False

    def active_run(self):
        if self.waited_for_release:
            return None
        return {"origin": "scheduled"}

    def request_stop_for_scheduled(self):
        self.stop_requested = True
        return True

    def wait_for_release(self, timeout):
        self.waited_for_release = timeout == 60
        return True


class _Accounts:
    def __init__(self):
        self.current = "original"
        self.selected = []
        self.entries = [
            {"id": "original", "label": "Original", "first_setup_done": True},
            {"id": "ready-a", "label": "Ready A", "first_setup_done": True},
            {"id": "pending", "label": "Pending", "first_setup_done": False},
            {"id": "ready-b", "label": "Ready B", "first_setup_done": True},
        ]

    def list(self):
        return list(self.entries)

    def current_id(self):
        return self.current

    def select(self, account_id):
        self.current = account_id
        self.selected.append(account_id)


class _GlobalSettings:
    def __init__(self):
        self.data = {}

    def get_settings(self):
        return dict(self.data)

    def save_settings(self, settings):
        self.data = dict(settings)


class BatchRunnerTests(unittest.TestCase):
    def _api(self, outcomes):
        api = AutoRewarderAPI.__new__(AutoRewarderAPI)
        api._run_lock = threading.Lock()
        api._stop_event = threading.Event()
        api._webview_window = None
        api.log = lambda _message: None
        api.account_manager = _Accounts()
        api.run_coordinator = _Coordinator()
        api._rebuild_account_context = lambda: None
        api._broadcast_account_ui = lambda: None
        api._notify_batch_ui = lambda *_args, **_kwargs: None
        api._get_account_schedule = lambda account_id: {
            "queries_pc": 11 if account_id == "ready-a" else 7,
            "queries_mobile": 4 if account_id == "ready-a" else 2,
            "advancedScheduling": account_id == "ready-b",
            "enabled": account_id == "ready-b",
        }
        api._is_account_completed_today = lambda account_id: account_id == "original"
        api.marked = []
        api._mark_account_completed_today = api.marked.append
        api.calls = []

        def run_current(pc, mobile, include_daily_tasks):
            account_id = api.account_manager.current_id()
            api.calls.append((account_id, pc, mobile, include_daily_tasks))
            return outcomes[account_id]

        api._run_current_account = run_current
        return api

    def test_batch_uses_each_ready_accounts_schedule_and_skips_today_marker(self):
        api = self._api(
            {
                "ready-a": {"completed": True, "stopped": False, "error": None},
                "ready-b": {"completed": True, "stopped": False, "error": None},
            }
        )

        result = api.run_all_accounts(include_daily_tasks=True)

        self.assertEqual("completed", result["status"])
        self.assertEqual(["ready-a", "ready-b"], result["completed_account_ids"])
        self.assertEqual(["original"], result["skipped_account_ids"])
        self.assertEqual([("ready-a", 11, 4, True), ("ready-b", 7, 2, True)], api.calls)
        self.assertEqual(["ready-a", "ready-b"], api.marked)
        self.assertEqual("original", api.account_manager.current_id())

    def test_batch_stops_on_first_unfinished_account_and_resumes_completed_marker(self):
        api = self._api(
            {
                "ready-a": {"completed": True, "stopped": False, "error": None},
                "ready-b": {
                    "completed": False,
                    "stopped": True,
                    "error": None,
                },
            }
        )

        result = api.run_all_accounts(include_daily_tasks=False)

        self.assertEqual("stopped", result["status"])
        self.assertEqual("ready-b", result["failed_account_id"])
        self.assertEqual(["ready-a"], api.marked)
        self.assertEqual(
            [("ready-a", 11, 4, False), ("ready-b", 7, 2, False)], api.calls
        )
        self.assertEqual("original", api.account_manager.current_id())

    def test_batch_requests_an_existing_scheduled_run_to_stop_before_acquiring_lock(self):
        api = self._api(
            {
                "ready-a": {"completed": True, "stopped": False, "error": None},
                "ready-b": {"completed": True, "stopped": False, "error": None},
            }
        )
        coordinator = _ScheduledRunCoordinator()
        api.run_coordinator = coordinator

        result = api.run_all_accounts()

        self.assertEqual("completed", result["status"])
        self.assertTrue(coordinator.stop_requested)
        self.assertTrue(coordinator.waited_for_release)
        self.assertEqual(["batch"], coordinator.origins)


class SingleAccountRunTests(unittest.TestCase):
    def _api(self):
        api = AutoRewarderAPI.__new__(AutoRewarderAPI)
        api._stop_event = threading.Event()
        api._session_counts = {"pc": 0, "mobile": 0, "cards": 0, "earn": 0, "quests": 0}
        api._last_scraped_balance = None
        api.log = lambda _message: None
        api._record_session_stats = lambda: None
        api._get_account_schedule = lambda _account_id: {
            "enabled": False,
            "advancedScheduling": False,
        }
        api.account_manager = _Accounts()
        api.run_coordinator = _Coordinator()
        api.run_origin = "interactive"
        return api

    def test_single_account_uses_existing_pc_mobile_phases_and_daily_flag(self):
        api = self._api()
        phases = []

        def run_phase(mobile, count, do_daily_set):
            phases.append((mobile, count, do_daily_set))
            return {
                "expected": count,
                "completed": count,
                "daily_success": True if do_daily_set else None,
                "error": None,
            }

        api._run_phase = run_phase

        result = api._run_current_account(11, 4, include_daily_tasks=True)

        self.assertTrue(result["completed"])
        self.assertEqual([(False, 11, True), (True, 4, False)], phases)

    def test_single_account_returns_the_existing_advanced_schedule_outcome(self):
        api = self._api()
        api._get_account_schedule = lambda _account_id: {
            "enabled": True,
            "advancedScheduling": True,
            "runDuration": 2,
            "queriesPerHour": 12,
        }
        expected = {
            "completed": True,
            "stopped": False,
            "error": None,
            "pc_completed": 11,
            "mobile_completed": 4,
            "daily_success": True,
        }
        api._run_advanced_schedule = lambda *_args: expected

        result = api._run_current_account(11, 4, include_daily_tasks=True)

        self.assertIs(expected, result)

    def test_main_marks_today_only_after_a_completed_outcome(self):
        api = self._api()
        api._run_lock = threading.Lock()
        api._webview_window = None
        api.account_meta = type(
            "Meta", (), {"is_first_setup_done": lambda self: True}
        )()
        api._run_current_account = lambda *_args, **_kwargs: {
            "completed": False,
            "stopped": True,
            "error": None,
        }
        marked = []
        api._mark_account_completed_today = lambda account_id: marked.append(account_id)

        result = api.main(11, 4)

        self.assertTrue(result["stopped"])
        self.assertEqual([], marked)

    def test_daily_only_run_does_not_mark_search_batch_completion(self):
        api = self._api()
        api._run_lock = threading.Lock()
        api._webview_window = None
        api.account_meta = type(
            "Meta", (), {"is_first_setup_done": lambda self: True}
        )()
        api._run_daily_only = lambda: {
            "completed": True,
            "stopped": False,
            "error": None,
        }
        marked = []
        api._mark_account_completed_today = lambda account_id: marked.append(account_id)

        result = api.main(0, 0, daily_only=True)

        self.assertTrue(result["completed"])
        self.assertEqual([], marked)

    def test_scheduled_main_owns_and_releases_a_scheduled_lease(self):
        api = self._api()
        api._run_lock = threading.Lock()
        api._webview_window = None
        api.account_meta = type(
            "Meta", (), {"is_first_setup_done": lambda self: True}
        )()
        api.run_origin = "scheduled"
        api._run_current_account = lambda *_args, **_kwargs: {
            "completed": True,
            "stopped": False,
            "error": None,
        }
        api._mark_account_completed_today = lambda _account_id: None

        result = api.main(11, 4)

        self.assertTrue(result["completed"])
        self.assertEqual(["scheduled"], api.run_coordinator.origins)
        self.assertTrue(api.run_coordinator.lease.released)

    def test_batch_daily_tasks_choice_is_persisted_for_the_gui(self):
        api = self._api()
        api.global_settings = _GlobalSettings()

        self.assertFalse(api.get_batch_include_daily_tasks())
        self.assertTrue(api.set_batch_include_daily_tasks(True))
        self.assertTrue(api.get_batch_include_daily_tasks())


if __name__ == "__main__":
    unittest.main()
