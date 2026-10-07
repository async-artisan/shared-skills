"""gitsync.py 单元测试：纯函数与预检逻辑，不调真实 git（避免污染真实仓配置）。

测试边界：覆盖 _redact、_commit_message、_stage_summary 的解析逻辑；
所有 git 调用都通过 monkey-patch 替换为桩函数。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

# SKILLSYNC_HOME 由 tests/unit/__init__.py 统一设置

from skillsync import gitsync as gitsync_mod  # noqa: E402
from skillsync.gitsync import (  # noqa: E402
    GitError, PreflightError, _commit_message, _redact, _stage_summary,
)


class TestRedact(unittest.TestCase):
    def test_redact_userinfo(self):
        url = "https://user:pass@github.com/owner/repo.git"
        redacted = _redact(url)
        self.assertIn("***@", redacted)
        self.assertNotIn("user:pass", redacted)
        self.assertIn("github.com/owner/repo.git", redacted)

    def test_no_userinfo_unchanged(self):
        url = "https://github.com/owner/repo.git"
        self.assertEqual(_redact(url), url)

    def test_ssh_url_redacted(self):
        url = "git@github.com:owner/repo.git"
        # git@ 在 @ 前面，正则匹配 (://)... 不命中，原样返回
        self.assertEqual(_redact(url), url)


class TestCommitMessage(unittest.TestCase):
    def test_added_skills(self):
        s = {"added": ["alpha", "beta"], "updated": [], "deleted": [],
             "others": [], "catalog_changed": False, "n_files": 2}
        msg = _commit_message(s)
        self.assertIn("新增技能 2 个", msg)
        self.assertIn("alpha", msg)
        self.assertIn("beta", msg)

    def test_updated_skills(self):
        s = {"added": [], "updated": ["alpha"], "deleted": [],
             "others": [], "catalog_changed": False, "n_files": 1}
        msg = _commit_message(s)
        self.assertIn("更新技能 1 个", msg)
        self.assertIn("alpha", msg)

    def test_deleted_skills(self):
        s = {"added": [], "updated": [], "deleted": ["gamma"],
             "others": [], "catalog_changed": False, "n_files": 1}
        msg = _commit_message(s)
        self.assertIn("移除技能 1 个", msg)
        self.assertIn("gamma", msg)

    def test_catalog_change(self):
        s = {"added": [], "updated": [], "deleted": [],
             "others": [], "catalog_changed": True, "n_files": 1}
        msg = _commit_message(s)
        self.assertIn("catalog 变更", msg)

    def test_others_change(self):
        s = {"added": [], "updated": [], "deleted": [],
             "others": ["tools/skillsync/x.py", "README.md"],
             "catalog_changed": False, "n_files": 2}
        msg = _commit_message(s)
        self.assertIn("仓骨架变更 2 项", msg)

    def test_empty_no_changes(self):
        s = {"added": [], "updated": [], "deleted": [],
             "others": [], "catalog_changed": False, "n_files": 0}
        msg = _commit_message(s)
        self.assertIn("空同步提交", msg)

    def test_combined_changes(self):
        s = {"added": ["alpha"], "updated": ["beta"], "deleted": ["gamma"],
             "others": [], "catalog_changed": True, "n_files": 3}
        msg = _commit_message(s)
        self.assertIn("新增技能 1 个", msg)
        self.assertIn("更新技能 1 个", msg)
        self.assertIn("移除技能 1 个", msg)
        self.assertIn("catalog 变更", msg)


class TestStageSummary(unittest.TestCase):
    """_stage_summary 调用 git diff --cached --name-status；用 monkey-patch 替换 _git。"""

    def _patch(self, stdout: str):
        return patch.object(gitsync_mod, "_git",
                            return_value=type("R", (), {"stdout": stdout}))

    def test_added_skill(self):
        with self._patch("A\tskills/alpha/SKILL.md\nA\tskills/alpha/references/x.md\n"):
            s = _stage_summary()
        self.assertEqual(s["added"], ["alpha"])
        self.assertFalse(s["catalog_changed"])

    def test_modified_skill(self):
        with self._patch("M\tskills/beta/SKILL.md\n"):
            s = _stage_summary()
        self.assertEqual(s["updated"], ["beta"])

    def test_deleted_skill(self):
        with self._patch("D\tskills/gamma/SKILL.md\n"):
            s = _stage_summary()
        self.assertEqual(s["deleted"], ["gamma"])

    def test_catalog_change(self):
        with self._patch("M\tregistry/catalog.yaml\n"):
            s = _stage_summary()
        self.assertTrue(s["catalog_changed"])

    def test_others_path(self):
        with self._patch("M\ttools/skillsync/x.py\nM\tREADME.md\n"):
            s = _stage_summary()
        self.assertEqual(s["others"], ["README.md", "tools/skillsync/x.py"])

    def test_empty(self):
        with self._patch(""):
            s = _stage_summary()
        self.assertEqual(s["added"], [])
        self.assertEqual(s["updated"], [])
        self.assertEqual(s["deleted"], [])
        self.assertEqual(s["others"], [])
        self.assertFalse(s["catalog_changed"])
        self.assertEqual(s["n_files"], 0)


def tearDownModule():
    pass


if __name__ == "__main__":
    unittest.main()
