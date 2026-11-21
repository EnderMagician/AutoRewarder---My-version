import random
import threading
import unittest
from unittest import mock

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.keys import Keys

from src import utils
from src.batch_eta import (
    DAILY_TASKS_SECONDS,
    PHASE_OVERHEAD_SECONDS,
    BatchEta,
    format_eta,
)
from src.batch_pacing import AccountPlan
from src.emulator import human as human_module
from src.emulator.human import WHEEL_NOTCH, HumanBehavior
from src.search import engine as engine_module
from src.search.engine import SearchEngine, expected_search_seconds, search_delay


def _plan(account_id, pc, mobile, gap=0.0):
    return AccountPlan(
        account={"id": account_id, "label": account_id.upper()},
        pc_count=pc,
        mobile_count=mobile,
        gap_before=gap,
        mobile_first=False,
    )


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class BatchEtaTests(unittest.TestCase):
    def test_initial_estimate_adds_searches_overheads_and_planned_gaps(self):
        eta = BatchEta(
            [_plan("a", 10, 5), _plan("b", 4, 0, gap=300)],
            seconds_per_search=40,
            phase_gap_mean=60,
            include_daily_tasks=True,
            clock=_Clock(),
        )
        expected = (
            19 * 40
            + 3 * PHASE_OVERHEAD_SECONDS
            + 60
            + 2 * DAILY_TASKS_SECONDS
            + 300
        )
        self.assertAlmostEqual(expected, eta.seconds_left())

    def test_measured_pace_pulls_the_estimate_and_phase_changes_are_not_timed(self):
        clock = _Clock()
        eta = BatchEta([_plan("a", 20, 0)], seconds_per_search=40, clock=clock)
        eta.start_account("a")
        eta.start_phase()
        for _ in range(11):
            clock.now += 100
            eta.search_done()
        # 10 measured gaps of 100 s against a prior of 40 s worth 6 searches.
        self.assertAlmostEqual((40 * 6 + 1000) / 16, eta.seconds_per_search())
        self.assertEqual(9, eta.remaining_searches())

        clock.now += 5000  # browser restart, Daily tasks...
        eta.start_phase()
        eta.search_done()
        self.assertAlmostEqual((40 * 6 + 1000) / 16, eta.seconds_per_search())

    def test_wait_counts_down_and_finished_accounts_drop_out(self):
        clock = _Clock()
        eta = BatchEta([_plan("a", 2, 0), _plan("b", 2, 0, gap=600)], 30, clock=clock)
        eta.start_account("a")
        eta.finish_account("a")
        eta.start_wait("b")
        before = eta.seconds_left()
        clock.now += 200
        self.assertAlmostEqual(before - 200, eta.seconds_left())
        clock.now += 1000
        eta.start_account("b")
        self.assertAlmostEqual(2 * 30 + PHASE_OVERHEAD_SECONDS, eta.seconds_left())

    def test_advanced_scheduling_accounts_count_their_duration(self):
        clock = _Clock()
        eta = BatchEta([_plan("a", 30, 20)], 30, timed_accounts={"a": 7200}, clock=clock)
        self.assertAlmostEqual(7200, eta.seconds_left())
        eta.start_account("a")
        clock.now += 1800
        self.assertAlmostEqual(5400, eta.seconds_left())

    def test_format_eta(self):
        self.assertEqual("under a minute", format_eta(20))
        self.assertEqual("12 min", format_eta(12 * 60 + 10))
        self.assertEqual("1 h 05 min", format_eta(3900))


class PacingMathTests(unittest.TestCase):
    def test_search_delay_stays_in_range_and_leans_low(self):
        random.seed(1)
        samples = [search_delay(4, 10) for _ in range(3000)]
        self.assertTrue(all(4 <= s <= 10 for s in samples))
        self.assertLess(sum(samples) / len(samples), 7)
        self.assertEqual(5, search_delay(5, 5))

    def test_expected_search_time_grows_with_delay_and_pauses(self):
        base = expected_search_seconds((4, 10), random_pauses=False)
        self.assertGreater(expected_search_seconds((20, 40), random_pauses=False), base)
        with_pauses = expected_search_seconds((4, 10), random_pauses=True)
        self.assertAlmostEqual(0.10 * 37.5 + 0.03 * 165, with_pauses - base)


class _Field:
    def __init__(self):
        self.text = ""
        self.keys = []

    def send_keys(self, *keys):
        for key in keys:
            self.keys.append(key)
            if key == Keys.BACKSPACE:
                self.text = self.text[:-1]
            else:
                self.text += key


class TypingTests(unittest.TestCase):
    def test_slips_are_corrected_so_the_query_comes_out_right(self):
        random.seed(3)
        field = _Field()
        with mock.patch.object(utils.time, "sleep"):
            utils.human_typing(field, "how to beat malenia", slip_chance=0.5)
        self.assertEqual("how to beat malenia", field.text)
        self.assertIn(Keys.BACKSPACE, field.keys)

    def test_neighbour_keys_are_adjacent(self):
        random.seed(0)
        for _ in range(50):
            self.assertIn(utils._neighbour_key("g"), set("fhtyvb"))
        self.assertIsNone(utils._neighbour_key("é"))

    def test_typing_speed_is_human(self):
        random.seed(5)
        delays = [utils.keystroke_delay("a", "b") for _ in range(4000)]
        mean = sum(delays) / len(delays)
        self.assertTrue(0.12 < mean < 0.22, mean)


class _ScrollDriver:
    """Just enough WebDriver for HumanBehavior's desktop scrolling."""

    def __init__(self):
        self.scroll_y = 0
        self.wheel_actions = 0
        self.js_scrolls = 0

    def execute_script(self, script, *args):
        if "innerWidth" in script:
            return [1920, 1080]
        if "scrollHeight" in script:
            return [5000, 1000]
        if "scrollY" in script:
            return self.scroll_y
        if "scrollBy" in script:
            self.js_scrolls += 1
        return None

    def execute(self, command, params=None):
        for source in (params or {}).get("actions", []):
            for action in source.get("actions", []):
                if action.get("type") == "scroll":
                    self.wheel_actions += 1
                    self.scroll_y += action["deltaY"]
        return {"value": None}


class ScrollTests(unittest.TestCase):
    def test_desktop_scroll_uses_wheel_ticks_until_the_target_depth(self):
        driver = _ScrollDriver()
        human = HumanBehavior(driver, mobile=False)
        with mock.patch.object(human_module.time, "sleep"), mock.patch.object(
            human_module.random, "random", return_value=0.9
        ):
            human.scroll_page()
        self.assertGreater(driver.wheel_actions, 0)
        self.assertEqual(0, driver.js_scrolls)
        self.assertGreaterEqual(driver.scroll_y, 5000 * 0.67 - 1000)
        self.assertEqual(0, driver.scroll_y % WHEEL_NOTCH)

    def test_cursor_overlay_is_off_by_default(self):
        self.assertFalse(HumanBehavior(_ScrollDriver()).show_cursor)


class _SearchDriver:
    def __init__(self):
        self.visits = []
        self.current_url = "https://www.bing.com/search?q=x"
        self.window_handles = ["main"]
        self.current_window_handle = "main"

    def get(self, url):
        self.visits.append(url)


class SearchLoopTests(unittest.TestCase):
    def _engine(self):
        engine = SearchEngine()
        engine.random_pauses = False
        engine.get_coffee_break_count = lambda: 99
        return engine

    def _patches(self, **constants):
        patchers = [mock.patch.object(engine_module.time, "sleep")]
        patchers += [mock.patch.object(engine_module, k, v) for k, v in constants.items()]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_follow_up_searches_refine_from_the_results_page(self):
        self._patches(REFINE_FROM_RESULTS=1.0, OPEN_RESULT_CHANCE=0.0)
        engine = self._engine()
        engine._enter_query = lambda *_a: None
        engine._browse_results = lambda *_a: "All"
        driver = _SearchDriver()
        done = []

        with mock.patch.object(engine_module, "HumanBehavior"):
            count = engine.perform_searches(driver, ["a", "b", "c"], on_search_done=done.append)

        self.assertEqual(3, count)
        self.assertEqual([engine_module.BING_HOME_URL], driver.visits)
        self.assertEqual([True, True, True], done)

    def test_back_to_the_homepage_after_another_tab_or_an_error(self):
        self._patches(REFINE_FROM_RESULTS=1.0, OPEN_RESULT_CHANCE=0.0)
        engine = self._engine()
        tabs = iter(["Images", "All", "All"])
        engine._browse_results = lambda *_a: next(tabs)
        calls = []

        def enter(*_a):
            calls.append(1)
            if len(calls) == 2:
                raise NoSuchElementException("no box")

        engine._enter_query = enter
        driver = _SearchDriver()
        done = []
        with mock.patch.object(engine_module, "HumanBehavior"):
            count = engine.perform_searches(driver, ["a", "b", "c"], on_search_done=done.append)

        self.assertEqual(2, count)
        self.assertEqual(3, len(driver.visits))
        self.assertEqual([True, False, True], done)

    def test_random_pause_logs_for_the_gui_and_stops_on_request(self):
        logs = []
        engine = SearchEngine(logger=logs.append)
        stop = threading.Event()
        stop.set()
        with mock.patch.object(engine_module.random, "random", return_value=0.0):
            self.assertTrue(engine._random_pause(stop))
        self.assertRegex(logs[-1], r"^Longer pause: sleeping for [\d.]+ seconds")

        engine.random_pauses = False
        with mock.patch.object(engine_module.random, "random", return_value=0.0):
            self.assertFalse(engine._random_pause(stop))


class BatchEtaWiringTests(unittest.TestCase):
    def test_batch_logs_time_left_and_clears_the_estimate(self):
        from tests.test_batch_runner import BatchRunnerTests

        def done(pc, mobile):
            return {
                "completed": True,
                "stopped": False,
                "error": None,
                "pc_completed": pc,
                "mobile_completed": mobile,
            }

        api = BatchRunnerTests._api(self, {"ready-a": done, "ready-b": done})
        estimates = []

        def run_current(pc, mobile, include_daily_tasks, **options):
            api.calls.append((api.account_manager.current_id(), pc, mobile))
            estimates.append(api._batch_eta.seconds_left())
            return done(pc, mobile)

        api._run_current_account = run_current
        result = api.run_all_accounts()

        self.assertEqual("completed", result["status"])
        self.assertIsNone(api._batch_eta)
        self.assertTrue(any(m.startswith("Batch time left: about") for m in api.logs))
        # The second account's estimate excludes the finished first one.
        self.assertGreater(estimates[0], estimates[1])
        # ready-b uses Advanced Scheduling (see the fixture), timed at 3 h.
        self.assertGreaterEqual(estimates[0], 3 * 3600)


if __name__ == "__main__":
    unittest.main()
