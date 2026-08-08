"""Regression checks for repository-native README badge configuration."""

import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
REPOSITORY = "EnderMagician/AutoRewarder---My-version"


class BadgeConfigurationTests(unittest.TestCase):
    def test_readme_uses_live_github_badges_without_a_secret_dependent_workflow(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(f"img.shields.io/github/stars/{REPOSITORY}", readme)
        self.assertIn(f"img.shields.io/github/downloads/{REPOSITORY}/total", readme)
        self.assertFalse((ROOT / ".github" / "workflows" / "badges.yml").exists())
        self.assertFalse((ROOT / "update_badges.py").exists())


if __name__ == "__main__":
    unittest.main()
