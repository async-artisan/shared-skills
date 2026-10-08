"""bootstrap.py 单元测试：扫描→下发→报告的全链路与各开关行为。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

from skillsync import bootstrap  # noqa: E402
from skillsync.core import PLATFORMS, SKILLS_DIR  # noqa: E402
from skillsync.store import load_catalog, save_catalog  # noqa: E402


class TestBootstrap(unittest.TestCase):
    def setUp(self):
        self.reg = SKILLS_DIR.parent / "registry"
        self.reg.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(SKILLS_DIR, ignore_errors=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        # 空 catalog + 空 state
        save_catalog({"version": 1, "skills": {}})

    def _make_canonical(self, slug: str, desc: str = "测试技能"):
        d = SKILLS_DIR / slug
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: {desc}\n---\n# {slug}\n",
            encoding="utf-8")

    def _register(self, slug: str, platforms=None):
        cat = load_catalog()
        cat["skills"][slug] = {
            "source_platform": "codex",
            "platforms": platforms or ["codex"],
            "tools_required": [],
            "depends_on": [],
            "description_zh": "测试",
            "adopted_at": "2026-10-07T00:00:00+08:00",
        }
        save_catalog(cat)

    def test_empty_env_dry_run(self):
        # catalog 为空：不会有任何软链需要下发；平台目录可能含真实散装技能，
        # 所以 adoptable/problem 可能非零，这里只断言 apply 侧为 0。
        r = bootstrap.run(dry_run=True)
        self.assertEqual(r.applied_n, 0)
        self.assertEqual(r.refused_n, 0)
        self.assertFalse(r.skip_apply)
        self.assertIsInstance(r.adoptable, list)
        self.assertIsInstance(r.problem_n, int)

    def test_skip_apply_does_not_run_apply(self):
        self._make_canonical("alpha")
        self._register("alpha", ["codex"])
        r = bootstrap.run(dry_run=False, skip_apply=True)
        # 平台 codex 目录不存在，所以未链接；apply 被跳过，applied 为 0
        self.assertTrue(r.skip_apply)
        self.assertEqual(r.apply_actions, [])
        self.assertEqual(r.applied_n, 0)

    def test_apply_creates_symlink_when_missing(self):
        self._make_canonical("beta")
        self._register("beta", ["codex"])
        # 确保平台目录存在但没有该技能
        codex_dir = PLATFORMS["codex"].skills_dir
        codex_dir.mkdir(parents=True, exist_ok=True)
        target = codex_dir / "beta"
        if target.exists() or target.is_symlink():
            target.unlink()
        r = bootstrap.run(dry_run=False)
        self.assertTrue(target.is_symlink())
        self.assertGreaterEqual(r.applied_n, 1)
        # cleanup
        if target.is_symlink():
            target.unlink()

    def test_dry_run_does_not_create_symlink(self):
        self._make_canonical("gamma")
        self._register("gamma", ["codex"])
        codex_dir = PLATFORMS["codex"].skills_dir
        codex_dir.mkdir(parents=True, exist_ok=True)
        target = codex_dir / "gamma"
        if target.exists() or target.is_symlink():
            target.unlink()
        r = bootstrap.run(dry_run=True)
        self.assertFalse(target.exists())
        self.assertFalse(target.is_symlink())
        # dry-run 下 actions 仍有 link-create 计划
        kinds = [a.kind for a in r.apply_actions]
        self.assertIn("link-create", kinds)

    def test_format_text_contains_key_sections(self):
        r = bootstrap.run(dry_run=True, skip_apply=True)
        text = bootstrap.format_text(r)
        self.assertIn("平台清单", text)
        self.assertIn("canonical 技能", text)
        self.assertIn("下一步", text)

    def test_format_text_skip_apply_message(self):
        r = bootstrap.run(dry_run=True, skip_apply=True)
        text = bootstrap.format_text(r)
        self.assertIn("跳过软链下发", text)


if __name__ == "__main__":
    unittest.main()
