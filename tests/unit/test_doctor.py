"""doctor.py 单元测试：Finding、scan_all、_classify、format_text。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

# SKILLSYNC_HOME 由 tests/unit/__init__.py 统一设置，确保 core.REPO_ROOT
# 在首次 import 时就锁定到隔离临时目录，避免污染真实仓

from skillsync.core import (  # noqa: E402
    REGISTRY_DIR, SKILLS_DIR, SKILL_MD,
)
from skillsync.store import save_catalog, save_state  # noqa: E402
from skillsync import doctor as doctor_mod  # noqa: E402
from skillsync.doctor import (  # noqa: E402
    ADOPTABLE_STATUSES, Finding, PROBLEM_STATUSES, STATUS_LABELS,
    _classify, _summarize, format_text, scan_all,
)


SKILL_MD_OK = """---
name: {name}
description: 测试 {name}
---
# {name}
"""


def _make_skill(slug: str, body: str = ""):
    d = SKILLS_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / SKILL_MD).write_text(SKILL_MD_OK.format(name=slug) + body, encoding="utf-8")


def _make_skill_md(name: str, description: str = "") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"


class TestFinding(unittest.TestCase):
    def test_default_archived_false(self):
        f = Finding(platform="codex", name="alpha", status="unregistered")
        self.assertFalse(f.archived)

    def test_explicit_archived_true(self):
        f = Finding(platform="codex", name="alpha", status="fork", archived=True)
        self.assertTrue(f.archived)


class TestScanAll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        # 清空 SKILLS_DIR
        import shutil
        if SKILLS_DIR.exists():
            shutil.rmtree(SKILLS_DIR)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        save_catalog({"version": 1, "skills": {}})
        save_state({"version": 1, "ignored": [], "last_apply": {}})

    def _make_canonical_only(self, slug: str):
        _make_skill(slug)
        return slug

    def test_empty_canonical_clean(self):
        # SKILLSYNC_HOME 重定向 canonical 目录；平台目录（~/.codex 等）由用户家目录决定，
        # 不在测试隔离范围内。本断言只关心 canonical 空时计数与归档统计为 0。
        result = scan_all()
        self.assertEqual(result["canonical_count"], 0)
        self.assertEqual(result["archived_n"], 0)
        # canonical 自身元数据体检问题应为 0（没有 canonical 技能）
        canon_issues = [f for f in result["findings"]
                        if f["platform"] == "-" and f["status"] == "bad-frontmatter"]
        self.assertEqual(canon_issues, [])

    def test_missing_canonical_when_catalog_has_unmatched_entry(self):
        save_catalog({"version": 1, "skills": {
            "ghost": {"source_platform": "codex", "platforms": ["codex"]}
        }})
        result = scan_all()
        missing = [f for f in result["findings"]
                   if f["status"] == "missing-canonical" and f["name"] == "ghost"]
        self.assertTrue(missing)
        self.assertGreaterEqual(len(missing), 1)
        self.assertIn("missing-canonical", result["summary"])

    def test_archived_marking_and_summary_exclusion(self):
        save_catalog({"version": 1, "skills": {
            "ghost": {"source_platform": "codex", "platforms": ["codex"],
                      "archived": True, "archived_at": "2026-10-07T00:00:00+08:00"}
        }})
        result = scan_all()
        # findings 仍含 missing-canonical 行，但带 archived 标志
        finds = [f for f in result["findings"] if f["name"] == "ghost"]
        self.assertTrue(finds)
        self.assertTrue(all(f["archived"] for f in finds))
        # summary 用未归档口径：missing-canonical 应为 0（归档的 ghost 不计）
        self.assertEqual(result["summary"].get("missing-canonical", 0), 0)
        self.assertGreaterEqual(result["archived_n"], 1)

    def test_format_text_includes_archived_marker(self):
        save_catalog({"version": 1, "skills": {
            "ghost": {"source_platform": "codex", "platforms": ["codex"],
                      "archived": True, "archived_at": "2026-10-07T00:00:00+08:00"}
        }})
        result = scan_all()
        text = format_text(result)
        # 归档行加 [已归档] 前缀
        self.assertIn("[已归档]", text)
        # 汇总行附「另有 N 条已归档」
        self.assertIn("另有", text)
        self.assertIn("已归档", text)


class TestStatusSets(unittest.TestCase):
    def test_problem_statuses_disjoint_from_adoptable(self):
        self.assertEqual(PROBLEM_STATUSES & ADOPTABLE_STATUSES, set())

    def test_status_labels_cover_all_problem_statuses(self):
        for s in PROBLEM_STATUSES:
            self.assertIn(s, STATUS_LABELS)

    def test_status_labels_cover_all_adoptable_statuses(self):
        for s in ADOPTABLE_STATUSES:
            self.assertIn(s, STATUS_LABELS)


def tearDownModule():
    pass


if __name__ == "__main__":
    unittest.main()
