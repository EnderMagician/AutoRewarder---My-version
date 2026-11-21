import random
import threading
import unittest

from src.api import AutoRewarderAPI
from src.batch_pacing import default_batch_pacing


def _pacing(enabled):
    pacing = default_batch_pacing()
    pacing["enabled"] = enabled
    return pacing


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
    def __init__(self, pacing=None):
        self.data = {}
        self.pacing = pacing or _pacing(False)

    def get_batch_pacing(self):
        return dict(self.pacing)

    def get_settings(self):
        return dict(self.data)

    def save_settings(self, settings):
        self.data = dict(settings)

    def get_manual_search_delay(self):
        return (2.0, 5.0)


class BatchRunnerTests(unittest.TestCase):
    def _api(self, outcomes, pacing=None):
        api = AutoRewarderAPI.__new__(AutoRewarderAPI)
        api._run_lock = threading.Lock()
        api._stop_event = threading.Event()
        api._webview_window = None
        api.logs = []
        api.log = api.logs.append
        api.global_settings = _GlobalSettings(pacing)
        api._batch_rng = random.Random(7)
        api.sleeps = []
        api._sleep_with_stop = lambda seconds: api.sleeps.append(seconds) or False
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

        api.call_options = []

        def run_current(pc, mobile, include_daily_tasks, **options):
            account_id = api.account_manager.current_id()
            api.calls.append((account_id, pc, mobile, include_daily_tasks))
            api.call_options.append(options)
            outcome = outcomes[account_id]
            if callable(outcome):
                return outcome(pc, mobile)
            return outcome

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
        self.assertEqual([], api.sleeps)
        self.assertEqual(
            [{"mobile_first": False, "phase_gap_range": None}] * 2, api.call_options
        )

    def test_batch_progress_excludes_completed_today_accounts_from_remaining_total(
        self,
    ):
        """Catch a UI counter that includes accounts skipped for today's marker."""
        api = self._api(
            {
                "ready-a": {"completed": True, "stopped": False, "error": None},
                "ready-b": {"completed": True, "stopped": False, "error": None},
            }
        )
        notifications = []

        def notify(state, account=None, completed=0, total=0, skipped=0):
            if state == "running":
                notifications.append((account, completed, total, skipped))

        api._notify_batch_ui = notify

        api.run_all_accounts()

        self.assertEqual(
            [
                (None, 0, 2, 1),
                ("Ready A", 0, 2, 1),
                ("Ready B", 1, 2, 1),
            ],
            notifications,
        )

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

    def test_batch_continues_after_daily_task_only_failure_and_reports_it(self):
        api = self._api(
            {
                "ready-a": {
                    "completed": False,
                    "stopped": False,
                    "error": "daily_tasks_failed",
                    "pc_completed": 11,
                    "mobile_completed": 4,
                },
                "ready-b": {
                    "completed": True,
                    "stopped": False,
                    "error": None,
                },
            }
        )

        result = api.run_all_accounts(include_daily_tasks=True)

        self.assertEqual("completed", result["status"])
        self.assertEqual(["ready-a", "ready-b"], result["completed_account_ids"])
        self.assertEqual(["ready-a", "ready-b"], api.marked)
        self.assertEqual(
            [{"id": "ready-a", "label": "Ready A"}],
            result["daily_task_failure_accounts"],
        )

    def test_batch_stops_when_daily_task_failure_leaves_searches_incomplete(self):
        api = self._api(
            {
                "ready-a": {
                    "completed": False,
                    "stopped": False,
                    "error": "daily_tasks_failed",
                    "pc_completed": 11,
                    "mobile_completed": 0,
                },
                "ready-b": {
                    "completed": True,
                    "stopped": False,
                    "error": None,
                },
            }
        )

        result = api.run_all_accounts(include_daily_tasks=True)

        self.assertEqual("failed", result["status"])
        self.assertEqual("ready-a", result["failed_account_id"])
        self.assertEqual([], api.marked)

    def test_batch_requests_an_existing_scheduled_run_to_stop_before_acquiring_lock(
        self,
    ):
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


class PacedBatchRunnerTests(unittest.TestCase):
    """Run all accounts with the default (humanized) pacing."""

    _api = BatchRunnerTests._api

    def _paced_api(self, outcomes=None, seed=7):
        def done(pc, mobile):
            return {
                "completed": True,
                "stopped": False,
                "error": None,
                "pc_completed": pc,
                "mobile_completed": mobile,
            }

        api = self._api(outcomes or {"ready-a": done, "ready-b": done}, _pacing(True))
        api._batch_rng = random.Random(seed)
        return api

    def test_paced_batch_waits_between_accounts_but_not_before_the_first(self):
        api = self._paced_api()
        notifications = []

        def notify(state, account=None, completed=0, total=0, skipped=0, **kw):
            notifications.append((state, account, kw.get("wait_seconds", 0)))

        api._notify_batch_ui = notify

        result = api.run_all_accounts()

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, len(api.sleeps))
        self.assertTrue(4 * 60 <= api.sleeps[0] <= 15 * 60, api.sleeps)
        waits = [n for n in notifications if n[0] == "waiting"]
        self.assertEqual(1, len(waits))
        labels = {"ready-a": "Ready A", "ready-b": "Ready B"}
        self.assertEqual(labels[api.calls[1][0]], waits[0][1])
        self.assertEqual(api.sleeps[0], waits[0][2])

    def test_paced_batch_only_adds_searches_within_the_variance(self):
        for seed in range(30):
            api = self._paced_api(seed=seed)
            api.run_all_accounts()
            configured = {"ready-a": (11, 4), "ready-b": (7, 2)}
            for account_id, pc, mobile, _daily in api.calls:
                base_pc, base_mobile = configured[account_id]
                self.assertTrue(base_pc <= pc <= base_pc + 2, (seed, pc))
                self.assertTrue(
                    base_mobile <= mobile <= base_mobile + 1, (seed, mobile)
                )
            self.assertEqual(sorted(api.marked), ["ready-a", "ready-b"])

    def test_paced_batch_changes_order_and_phase_order_across_runs(self):
        orders = set()
        mobile_first = set()
        for seed in range(30):
            api = self._paced_api(seed=seed)
            api.run_all_accounts()
            orders.add(tuple(call[0] for call in api.calls))
            for options in api.call_options:
                mobile_first.add(options["mobile_first"])
                self.assertEqual((30.0, 180.0), options["phase_gap_range"])
        self.assertEqual({("ready-a", "ready-b"), ("ready-b", "ready-a")}, orders)
        self.assertEqual({True, False}, mobile_first)

    def test_stop_during_the_wait_ends_the_batch_without_running_the_next_account(
        self,
    ):
        api = self._paced_api()
        api._sleep_with_stop = lambda seconds: api.sleeps.append(seconds) or True

        result = api.run_all_accounts()

        self.assertEqual("stopped", result["status"])
        self.assertIsNone(result["failed_account_id"])
        self.assertEqual(1, len(api.calls))
        self.assertEqual([api.calls[0][0]], api.marked)
        self.assertEqual("original", api.account_manager.current_id())

    def test_daily_task_only_failure_is_judged_against_the_varied_counts(self):
        def daily_failed(pc, mobile):
            return {
                "completed": False,
                "stopped": False,
                "error": "daily_tasks_failed",
                "pc_completed": pc,
                "mobile_completed": mobile,
            }

        api = self._paced_api({"ready-a": daily_failed, "ready-b": daily_failed})

        result = api.run_all_accounts(include_daily_tasks=True)

        self.assertEqual("completed", result["status"])
        self.assertEqual(2, len(result["daily_task_failure_accounts"]))


class SingleAccountRunTests(unittest.TestCase):
    def _api(self):
        api = AutoRewarderAPI.__new__(AutoRewarderAPI)
        api._stop_event = threading.Event()
        api._session_counts = {"pc": 0, "mobile": 0, "cards": 0, "earn": 0, "quests": 0}
        api._last_scraped_balance = None
        api.log = lambda _message: None
        api._record_session_stats = lambda *_args, **_kwargs: None
        api._get_account_schedule = lambda _account_id: {
            "enabled": False,
            "advancedScheduling": False,
        }
        api.account_manager = _Accounts()
        api.run_coordinator = _Coordinator()
        api.run_origin = "interactive"
        api.global_settings = _GlobalSettings()
        return api

    def test_single_account_uses_existing_pc_mobile_phases_and_daily_flag(self):
        api = self._api()
        phases = []
        delays = []

        def run_phase(mobile, count, do_daily_set, search_delay_range=None):
            phases.append((mobile, count, do_daily_set))
            delays.append(search_delay_range)
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
        self.assertEqual([(2.0, 5.0), (2.0, 5.0)], delays)

    def test_mobile_first_runs_mobile_then_pc_with_daily_tasks_after_a_pause(self):
        api = self._api()
        api._batch_rng = random.Random(3)
        sleeps = []
        api._sleep_with_stop = lambda seconds: sleeps.append(seconds) or False
        phases = []

        def run_phase(mobile, count, do_daily_set, search_delay_range=None):
            phases.append((mobile, count, do_daily_set))
            return {
                "expected": count,
                "completed": count,
                "daily_success": True if do_daily_set else None,
                "error": None,
            }

        api._run_phase = run_phase

        result = api._run_current_account(
            11, 4, include_daily_tasks=True, mobile_first=True, phase_gap_range=(30, 90)
        )

        self.assertTrue(result["completed"])
        self.assertEqual([(True, 4, False), (False, 11, True)], phases)
        self.assertEqual(1, len(sleeps))
        self.assertTrue(30 <= sleeps[0] <= 90)

    def test_stop_during_the_phase_pause_skips_the_second_phase(self):
        api = self._api()
        api._sleep_with_stop = lambda _seconds: True
        phases = []

        def run_phase(mobile, count, do_daily_set, search_delay_range=None):
            phases.append(mobile)
            return {
                "expected": count,
                "completed": count,
                "daily_success": None,
                "error": None,
            }

        api._run_phase = run_phase

        result = api._run_current_account(11, 4, phase_gap_range=(30, 90))

        self.assertTrue(result["stopped"])
        self.assertFalse(result["completed"])
        self.assertEqual([False], phases)

    def test_single_account_finishes_mobile_searches_after_daily_task_failure(self):
        api = self._api()
        phases = []
        delays = []

        def run_phase(mobile, count, do_daily_set, search_delay_range=None):
            phases.append((mobile, count, do_daily_set))
            delays.append(search_delay_range)
            return {
                "expected": count,
                "completed": count,
                "daily_success": False if do_daily_set else None,
                "error": "daily_tasks_failed" if do_daily_set else None,
            }

        api._run_phase = run_phase

        result = api._run_current_account(11, 4, include_daily_tasks=True)

        self.assertFalse(result["completed"])
        self.assertEqual("daily_tasks_failed", result["error"])
        self.assertEqual(11, result["pc_completed"])
        self.assertEqual(4, result["mobile_completed"])
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
