"""Regression checks for the Daily Task control affordances."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class DailyTaskToggleTests(unittest.TestCase):
    def test_daily_task_controls_render_a_visible_toggle_pill(self):
        page = (ROOT / "gui" / "index.html").read_text(encoding="utf-8")

        for toggle_id in ("dailyOnlyToggle", "batchDailyTasksToggle"):
            self.assertRegex(
                page,
                re.compile(
                    rf'<input type="checkbox" id="{toggle_id}">\s*'
                    r'<span class="toggle-pill" aria-hidden="true"></span>'
                ),
            )


if __name__ == "__main__":
    unittest.main()
