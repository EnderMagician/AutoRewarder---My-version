import unittest
from unittest.mock import patch

import AutoRewarder_CLI as cli


class _Meta:
    schedule = {
        "enabled": True,
        "advancedScheduling": False,
        "runDuration": 1,
        "queriesPerHour": 60,
        "queries_pc": 11,
        "queries_mobile": 0,
        "last_triggered_date": "",
    }

    def __init__(self, _account_id):
        pass

    def get_schedule(self):
        return dict(self.schedule)

    def set_schedule(self, schedule):
        type(self).schedule = dict(schedule)


class _Accounts:
    def current_id(self):
        return "ready-a"


class _Api:
    def __init__(self, outcome):
        self.account_manager = _Accounts()
        self.outcome = outcome
        self.calls = []
        self.history = None
        self.daily_set = None
        self.search_engine = None
        self.stats = None

    def main(self, pc, mobile):
        self.calls.append((pc, mobile))
        return self.outcome


class CliRunnerTests(unittest.TestCase):
    account = {"id": "ready-a", "label": "Ready A", "first_setup_done": True}

    def setUp(self):
        _Meta.schedule = {
            "enabled": True,
            "advancedScheduling": False,
            "runDuration": 1,
            "queriesPerHour": 60,
            "queries_pc": 11,
            "queries_mobile": 0,
            "last_triggered_date": "",
        }

    def test_incomplete_cli_run_does_not_write_today_marker(self):
        api = _Api({"completed": False, "stopped": False, "error": "phase_failed"})

        with patch.object(cli, "AccountMetaManager", _Meta), patch.object(
            cli, "console_log"
        ):
            did_run = cli._run_account(api, self.account)

        self.assertTrue(did_run)
        self.assertEqual("", _Meta.schedule["last_triggered_date"])

    def test_advanced_schedule_is_delegated_once_to_the_api_pipeline(self):
        _Meta.schedule["advancedScheduling"] = True
        api = _Api({"completed": True, "stopped": False, "error": None})

        with patch.object(cli, "AccountMetaManager", _Meta), patch.object(
            cli, "console_log"
        ):
            cli._run_account(api, self.account)

        self.assertEqual([(11, 0)], api.calls)


if __name__ == "__main__":
    unittest.main()
