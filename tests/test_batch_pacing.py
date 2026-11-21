import json
import os
import random
import tempfile
import unittest
from unittest import mock

from src import batch_pacing
from src.accounts import settings as settings_module
from src.batch_pacing import (
    default_batch_pacing,
    format_duration,
    normalize_batch_pacing,
    plan_batch,
    sample_pause,
    validate_batch_pacing,
    vary_count,
)

ACCOUNTS = [
    {"id": "a", "label": "A"},
    {"id": "b", "label": "B"},
    {"id": "c", "label": "C"},
]
COUNTS = {"a": (30, 20), "b": (10, 0), "c": (130, 99)}


class PlanBatchTests(unittest.TestCase):
    def test_disabled_pacing_keeps_the_old_back_to_back_batch(self):
        pacing = {**default_batch_pacing(), "enabled": False}

        plans = plan_batch(ACCOUNTS, COUNTS, pacing, rng=random.Random(1))

        self.assertEqual(["a", "b", "c"], [p.account["id"] for p in plans])
        self.assertEqual(
            [(30, 20), (10, 0), (130, 99)],
            [(p.pc_count, p.mobile_count) for p in plans],
        )
        self.assertEqual([0.0, 0.0, 0.0], [p.gap_before for p in plans])
        self.assertFalse(any(p.mobile_first for p in plans))

    def test_enabled_pacing_respects_counts_caps_and_gap_range(self):
        pacing = default_batch_pacing()
        for seed in range(50):
            plans = plan_batch(ACCOUNTS, COUNTS, pacing, rng=random.Random(seed))
            self.assertEqual(0.0, plans[0].gap_before)
            for plan in plans[1:]:
                self.assertTrue(4 * 60 <= plan.gap_before <= 15 * 60)
            for plan in plans:
                base_pc, base_mobile = COUNTS[plan.account["id"]]
                self.assertTrue(base_pc <= plan.pc_count <= min(130, base_pc + 5))
                self.assertTrue(
                    base_mobile <= plan.mobile_count <= min(99, base_mobile + 3)
                )
            self.assertEqual(["a", "b", "c"], sorted(p.account["id"] for p in plans))

    def test_shuffle_can_be_turned_off_on_its_own(self):
        pacing = {**default_batch_pacing(), "shuffle_accounts": False}
        for seed in range(20):
            plans = plan_batch(ACCOUNTS, COUNTS, pacing, rng=random.Random(seed))
            self.assertEqual(["a", "b", "c"], [p.account["id"] for p in plans])

    def test_phase_order_mixing_can_be_turned_off_on_its_own(self):
        pacing = {**default_batch_pacing(), "randomize_phase_order": False}
        for seed in range(20):
            plans = plan_batch(ACCOUNTS, COUNTS, pacing, rng=random.Random(seed))
            self.assertFalse(any(p.mobile_first for p in plans))

    def test_does_not_reorder_the_callers_list(self):
        accounts = list(ACCOUNTS)
        plan_batch(accounts, COUNTS, default_batch_pacing(), rng=random.Random(4))
        self.assertEqual(ACCOUNTS, accounts)


class HelperTests(unittest.TestCase):
    def test_vary_count_never_goes_below_base_and_keeps_zero_off(self):
        rng = random.Random(0)
        for _ in range(200):
            self.assertTrue(20 <= vary_count(20, 15, 99, rng) <= 23)
            self.assertEqual(0, vary_count(0, 50, 99, rng))
            self.assertEqual(99, vary_count(99, 50, 99, rng))
        self.assertEqual(20, vary_count(20, 0, 99, rng))

    def test_sample_pause_stays_in_range_and_leans_short(self):
        rng = random.Random(0)
        samples = [sample_pause(10, 110, rng) for _ in range(2000)]
        self.assertTrue(all(10 <= s <= 110 for s in samples))
        self.assertLess(sum(samples) / len(samples), 60)
        self.assertEqual(5, sample_pause(5, 5, rng))

    def test_format_duration(self):
        self.assertEqual("45s", format_duration(45.2))
        self.assertEqual("7m 05s", format_duration(425))
        self.assertEqual("1h 02m", format_duration(3720))


class ValidationTests(unittest.TestCase):
    def test_normalize_replaces_bad_values_with_defaults(self):
        pacing = normalize_batch_pacing(
            {
                "enabled": 0,
                "account_gap_min": 20,
                "account_gap_max": 5,
                "phase_gap_min": "x",
                "count_variance": 90,
            }
        )
        self.assertFalse(pacing["enabled"])
        self.assertEqual(
            (4.0, 15.0), (pacing["account_gap_min"], pacing["account_gap_max"])
        )
        self.assertEqual(30.0, pacing["phase_gap_min"])
        self.assertEqual(15, pacing["count_variance"])
        self.assertEqual(default_batch_pacing(), normalize_batch_pacing(None))

    def test_validate_rejects_instead_of_substituting(self):
        good = {**default_batch_pacing(), "account_gap_min": 1, "account_gap_max": 2}
        self.assertEqual(1.0, validate_batch_pacing(good)["account_gap_min"])
        for bad in (
            {"account_gap_min": 20, "account_gap_max": 5},
            {"account_gap_max": batch_pacing.MAX_ACCOUNT_GAP_MINUTES + 1},
            {"phase_gap_min": -1},
            {"count_variance": 12.5},
            {"count_variance": "lots"},
        ):
            self.assertIsNone(
                validate_batch_pacing({**default_batch_pacing(), **bad}), bad
            )


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(settings_module, "APP_DIR", self.tmp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = settings_module.GlobalSettingsManager()
        self.manager.path = os.path.join(self.tmp.name, "settings.json")

    def test_defaults_and_partial_update_round_trip(self):
        self.assertEqual(default_batch_pacing(), self.manager.get_batch_pacing())

        self.assertTrue(self.manager.set_batch_pacing({"account_gap_max": 30}))

        pacing = self.manager.get_batch_pacing()
        self.assertEqual(30.0, pacing["account_gap_max"])
        self.assertEqual(4.0, pacing["account_gap_min"])
        with open(self.manager.path, encoding="utf-8") as file:
            self.assertEqual(30.0, json.load(file)["batch_pacing"]["account_gap_max"])

    def test_invalid_update_is_refused_and_not_saved(self):
        self.assertFalse(
            self.manager.set_batch_pacing({"account_gap_min": 40, "account_gap_max": 2})
        )
        self.assertFalse(self.manager.set_batch_pacing("fast"))
        self.assertEqual(default_batch_pacing(), self.manager.get_batch_pacing())


if __name__ == "__main__":
    unittest.main()
