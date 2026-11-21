import random
import threading
import unittest
from collections import Counter
from datetime import date
from unittest import mock

from src import api as api_module
from src.api import AutoRewarderAPI
from src.config import SEARCH_PROFILES_PATH
from src.emulator.driver import DriverManager
from src.search import llm
from src.search.profiles import (
    _interest_clusters,
    blend_general_queries,
    build_profile_streaks,
    choose_profile,
    find_profile,
    load_profiles,
    recent_queries,
)

PROFILES = load_profiles(SEARCH_PROFILES_PATH)
TODAY = date(2026, 11, 22)


def _all_queries(profile):
    return {
        q for interest in profile["interests"] for c in _interest_clusters(interest) for q in c
    }


def _history(queries, day="11-21-2026"):
    return [{"date": day, "time": "10:00:00", "query": q, "status": "Success"} for q in queries]


class ProfileDataTests(unittest.TestCase):
    def test_ten_profiles_each_with_a_game_movies_manga_and_general_interest(self):
        self.assertEqual(10, len(PROFILES))
        self.assertEqual(10, len({p["id"] for p in PROFILES}))
        for profile in PROFILES:
            kinds = [i["kind"] for i in profile["interests"]]
            self.assertEqual(["game", "movies", "manga", "general"], kinds, profile["id"])
            for interest in profile["interests"]:
                self.assertTrue(interest.get("about"), interest["name"])

    def test_every_profile_has_enough_distinct_well_formed_queries(self):
        for profile in PROFILES:
            queries = _all_queries(profile)
            self.assertGreaterEqual(len(queries), 150, profile["id"])
            for query in queries:
                self.assertNotIn("{", query)
                self.assertEqual(query, query.strip())
                self.assertLessEqual(len(query.split()), 10, query)

    def test_profiles_do_not_share_queries(self):
        owners = Counter(q for p in PROFILES for q in _all_queries(p))
        shared = [q for q, n in owners.items() if n > 1]
        self.assertEqual([], shared)


class BuildStreaksTests(unittest.TestCase):
    def setUp(self):
        self.profile = find_profile(PROFILES, "dark-fantasy")

    def test_builds_the_requested_count_of_distinct_profile_queries(self):
        for seed in range(20):
            streaks = build_profile_streaks(self.profile, 40, rng=random.Random(seed), today=TODAY)
            flat = [q for s in streaks for q in s]
            self.assertEqual(40, len(flat))
            self.assertEqual(40, len({q.lower() for q in flat}))
            self.assertTrue(set(flat) <= _all_queries(self.profile))
            self.assertTrue(all(1 <= len(s) <= 3 for s in streaks))

    def test_a_streak_stays_on_one_item(self):
        clusters = [
            set(c) for i in self.profile["interests"] for c in _interest_clusters(i)
        ]
        streaks = build_profile_streaks(self.profile, 60, rng=random.Random(2), today=TODAY)
        for streak in streaks:
            self.assertTrue(any(set(streak) <= c for c in clusters), streak)

    def test_most_runs_touch_every_interest(self):
        names = {}
        for interest in self.profile["interests"]:
            for c in _interest_clusters(interest):
                for q in c:
                    names[q] = interest["name"]
        # A day without one interest is fine; most days should touch them all.
        complete = 0
        for seed in range(20):
            flat = [q for s in build_profile_streaks(self.profile, 35, rng=random.Random(seed)) for q in s]
            complete += len({names[q] for q in flat}) == 4
        self.assertGreaterEqual(complete, 15)

    def test_recent_searches_are_held_back_while_fresh_ones_remain(self):
        first = [q for s in build_profile_streaks(self.profile, 50, rng=random.Random(1)) for q in s]
        second = [
            q
            for s in build_profile_streaks(
                self.profile, 50, history=_history(first), rng=random.Random(9), today=TODAY
            )
            for q in s
        ]
        self.assertEqual(set(), set(first) & set(second))

    def test_window_shrinks_when_the_profile_runs_short(self):
        everything = sorted(_all_queries(self.profile))
        streaks = build_profile_streaks(
            self.profile, 30, history=_history(everything), rng=random.Random(4), today=TODAY
        )
        self.assertEqual(30, sum(len(s) for s in streaks))

    def test_mobile_leans_toward_everyday_questions(self):
        general = set()
        for c in _interest_clusters(self.profile["interests"][3]):
            general.update(c)

        def share(mobile):
            total = 0
            for seed in range(200):
                flat = [
                    q
                    for s in build_profile_streaks(self.profile, 20, mobile=mobile, rng=random.Random(seed))
                    for q in s
                ]
                total += sum(q in general for q in flat)
            return total

        self.assertGreater(share(True), share(False))


class HelperTests(unittest.TestCase):
    def test_choose_profile_keeps_accounts_apart_until_profiles_run_out(self):
        rng = random.Random(0)
        taken = []
        for _ in range(10):
            taken.append(choose_profile(PROFILES, taken, rng))
        self.assertEqual(10, len(set(taken)))
        eleventh = choose_profile(PROFILES, taken, rng)
        self.assertIn(eleventh, taken)
        self.assertIsNone(choose_profile([], [], rng))

    def test_blend_never_splits_a_streak(self):
        streaks = [["a1", "a2", "a3"], ["b1", "b2"], ["c1"]]
        for seed in range(30):
            flat = blend_general_queries(streaks, ["x", "y"], rng=random.Random(seed))
            self.assertEqual(8, len(flat))
            text = " ".join(flat)
            self.assertIn("a1 a2 a3", text)
            self.assertIn("b1 b2", text)

    def test_recent_queries_reads_history_dates_and_skips_bad_records(self):
        history = _history(["New One"], "11-21-2026") + _history(["old one"], "10-01-2026")
        history.append({"date": "garbage", "query": "x"})
        history.append({"query": "no date"})
        self.assertEqual({"new one"}, recent_queries(history, 10, TODAY))
        self.assertEqual(set(), recent_queries(history, 0, TODAY))


class _Meta:
    store: dict[str, str] = {}

    def __init__(self, account_id):
        self.account_id = account_id

    def get_search_profile(self):
        return self.store.get(self.account_id, "auto")

    def set_search_profile(self, profile_id):
        self.store[self.account_id] = profile_id
        return True


class _Accounts:
    def __init__(self, ids):
        self.ids = ids
        self.current = ids[0]

    def list(self):
        return [{"id": i, "label": i} for i in self.ids]

    def current_id(self):
        return self.current

    def exists(self, account_id):
        return account_id in self.ids


class _Settings:
    def __init__(self, llm_on=False):
        self.llm_on = llm_on

    def get_llm_config(self):
        return {
            "use_llm_queries": self.llm_on,
            "llm_api_key": "key" if self.llm_on else "",
            "llm_provider": "openai",
            "llm_model": "",
            "llm_base_url": "",
        }

    def get_settings(self):
        return {}


class _Engine:
    def load_queries_from_json(self, _path, num_needed):
        return [f"general query {i}" for i in range(num_needed)]


class _History:
    def get_history(self):
        return []


class ApiProfileTests(unittest.TestCase):
    def setUp(self):
        _Meta.store = {}
        for target, value in (
            (api_module, "AccountMetaManager"),
            ("src.utils.humanize_queries", None),
        ):
            if value:
                patcher = mock.patch.object(target, value, _Meta)
            else:
                patcher = mock.patch(target, lambda queries: list(queries))
            patcher.start()
            self.addCleanup(patcher.stop)

    def _api(self, ids=("a", "b"), llm_on=False):
        api = AutoRewarderAPI.__new__(AutoRewarderAPI)
        api._stop_event = threading.Event()
        api.logs = []
        api.log = api.logs.append
        api.account_manager = _Accounts(list(ids))
        api.global_settings = _Settings(llm_on)
        api.search_engine = _Engine()
        api.history = _History()
        return api

    def test_each_account_gets_its_own_profile_and_keeps_it(self):
        api = self._api(ids=[f"acc{i}" for i in range(10)])
        assigned = []
        for account_id in api.account_manager.ids:
            api.account_manager.current = account_id
            assigned.append(api._resolve_search_profile()["id"])
        self.assertEqual(10, len(set(assigned)))
        api.account_manager.current = "acc3"
        self.assertEqual(assigned[3], api._resolve_search_profile()["id"])

    def test_random_setting_keeps_the_old_random_list(self):
        api = self._api()
        _Meta.store["a"] = "random"
        self.assertIsNone(api._resolve_search_profile())
        self.assertEqual(
            ["general query 0", "general query 1"], api._build_queries(2)
        )

    def test_profile_run_is_mostly_themed_with_a_few_general_queries(self):
        api = self._api()
        _Meta.store["a"] = "horror"
        queries = api._build_queries(40, mobile=True)
        profile = find_profile(PROFILES, "horror")
        themed = [q for q in queries if q in _all_queries(profile)]
        general = [q for q in queries if q.startswith("general query")]
        self.assertEqual(40, len(queries))
        self.assertEqual(40, len(set(queries)))
        self.assertEqual(40, len(themed) + len(general))
        self.assertTrue(2 <= len(general) <= 6, len(general))

    def test_llm_gets_the_profile_interests_and_profile_tops_up(self):
        api = self._api(llm_on=True)
        _Meta.store["a"] = "cozy"
        seen = {}

        def fake_generate(count, locale, **kwargs):
            seen.update(kwargs)
            return ["llm one", "llm two"]

        with mock.patch.object(api_module.llm, "generate_queries", fake_generate), mock.patch.object(
            api_module, "resolve_search_locale", lambda *_a: "en-US"
        ):
            queries = api._build_queries(10)

        self.assertIn("the farming video game Stardew Valley", seen["interests"])
        self.assertEqual(["llm one", "llm two"], queries[:2])
        self.assertEqual(10, len(queries))

    def test_set_search_profile_validates_the_id(self):
        api = self._api()
        self.assertTrue(api.set_search_profile("a", "sci-fi"))
        self.assertTrue(api.set_search_profile("a", "random"))
        self.assertFalse(api.set_search_profile("a", "nope"))
        self.assertFalse(api.set_search_profile("missing", "sci-fi"))
        self.assertEqual(10, len(api.get_search_profiles()))


class LlmPromptTests(unittest.TestCase):
    def test_prompt_names_the_interests_when_given(self):
        prompt = llm._build_prompt(20, "en-US", ["the video game Elden Ring"])
        self.assertIn("the video game Elden Ring", prompt)
        self.assertIn("20", prompt)
        self.assertNotIn("Elden", llm._build_prompt(20, "en-US"))


class MobileDriverTests(unittest.TestCase):
    def test_mobile_session_looks_like_ios_26_safari_without_client_hints(self):
        calls = {}
        fake_driver = mock.Mock()
        fake_driver.execute_cdp_cmd.side_effect = lambda name, params: calls.__setitem__(name, params)

        with mock.patch("src.emulator.driver.webdriver.Edge", return_value=fake_driver) as edge:
            DriverManager(profile_path="/tmp/profile").setup_driver(mobile=True)

        args = edge.call_args.kwargs["options"].arguments
        self.assertIn("--window-size=393,852", args)
        ua = calls["Emulation.setUserAgentOverride"]
        self.assertIn("iPhone OS 18_6 like Mac OS X", ua["userAgent"])
        self.assertIn("Version/26.0 Mobile/15E148 Safari/604.1", ua["userAgent"])
        self.assertNotIn("userAgentMetadata", ua)
        metrics = calls["Emulation.setDeviceMetricsOverride"]
        self.assertEqual((393, 852, 3), (metrics["width"], metrics["height"], metrics["deviceScaleFactor"]))


if __name__ == "__main__":
    unittest.main()
