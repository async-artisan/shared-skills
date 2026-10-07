"""core.py 单元测试：哈希、frontmatter 解析、扫描、描述提取。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

from skillsync.core import (  # noqa: E402
    SKILLS_DIR,
    SKILL_MD,
    extract_description,
    frontmatter_issue,
    hash_skill,
    iter_skill_files,
    parse_frontmatter,
)


SKILL_MD_OK = """---
name: alpha
description: 测试技能 alpha
---
# alpha
正文
"""

SKILL_MD_NO_DESC = """---
name: alpha
description: ""
---
# alpha
"""

SKILL_MD_NO_FRONT = """# alpha
正文
"""

SKILL_MD_UNCLOSED = """---
name: alpha
description: 测试
# 缺少结束分隔符
"""

SKILL_MD_NAME_MISMATCH = """---
name: wrong-name
description: 测试
---
"""


class TestExtractDescription(unittest.TestCase):
    def test_simple_description(self):
        self.assertEqual(extract_description(SKILL_MD_OK), "测试技能 alpha")

    def test_empty_description(self):
        self.assertEqual(extract_description(SKILL_MD_NO_DESC), "")

    def test_no_frontmatter(self):
        self.assertEqual(extract_description(SKILL_MD_NO_FRONT), "")

    def test_quoted_string(self):
        md = '---\nname: x\ndescription: "hello world"\n---\n'
        self.assertEqual(extract_description(md), "hello world")

    def test_folded_block_scalar(self):
        md = '---\nname: x\ndescription: >\n  多行\n  介绍\n---\n'
        self.assertEqual(extract_description(md), "多行 介绍")


class TestFrontmatterIssue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "alpha"
        self.dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, content: str):
        (self.dir / SKILL_MD).write_text(content, encoding="utf-8")

    def test_ok_skill_no_issue(self):
        self._write(SKILL_MD_OK)
        self.assertEqual(frontmatter_issue(self.dir), "")

    def test_no_frontmatter(self):
        self._write(SKILL_MD_NO_FRONT)
        self.assertIn("frontmatter", frontmatter_issue(self.dir))

    def test_missing_description(self):
        self._write(SKILL_MD_NO_DESC)
        issue = frontmatter_issue(self.dir)
        self.assertIn("description", issue)

    def test_unclosed_frontmatter(self):
        self._write(SKILL_MD_UNCLOSED)
        self.assertIn("闭合", frontmatter_issue(self.dir))

    def test_name_mismatch(self):
        self._write(SKILL_MD_NAME_MISMATCH)
        issue = frontmatter_issue(self.dir)
        self.assertIn("不一致", issue)


class TestParseFrontmatter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "alpha"
        self.dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, content: str):
        (self.dir / SKILL_MD).write_text(content, encoding="utf-8")

    def test_valid_returns_dict(self):
        self._write(SKILL_MD_OK)
        data, err = parse_frontmatter(self.dir)
        self.assertIsNone(err)
        self.assertEqual(data["name"], "alpha")
        self.assertEqual(data["description"], "测试技能 alpha")

    def test_missing_skill_md(self):
        data, err = parse_frontmatter(self.dir)
        self.assertIsNone(data)
        self.assertIn("无法读取", err)


class TestHashSkill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / SKILL_MD).write_text(SKILL_MD_OK, encoding="utf-8")
        (self.root / "references").mkdir()
        (self.root / "references" / "extra.md").write_text("额外文件\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_hash_deterministic(self):
        h1 = hash_skill(self.root)
        h2 = hash_skill(self.root)
        self.assertEqual(h1, h2)

    def test_hash_changes_on_content_edit(self):
        h1 = hash_skill(self.root)
        (self.root / SKILL_MD).write_text(SKILL_MD_OK + "\n新内容\n", encoding="utf-8")
        h2 = hash_skill(self.root)
        self.assertNotEqual(h1, h2)

    def test_hash_changes_on_file_added(self):
        h1 = hash_skill(self.root)
        (self.root / "new.md").write_text("新\n", encoding="utf-8")
        h2 = hash_skill(self.root)
        self.assertNotEqual(h1, h2)

    def test_hash_ignores_bak_dir(self):
        # .bak-* 目录不在 iter_skill_files 中跳过，但 IGNORE_DIRS 含 __pycache__/.git
        h1 = hash_skill(self.root)
        # 新增 __pycache__ 不应影响哈希
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "x.pyc").write_text("ignored", encoding="utf-8")
        h2 = hash_skill(self.root)
        self.assertEqual(h1, h2)


class TestIterSkillFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.md").write_text("a", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.md").write_text("b", encoding="utf-8")
        (self.root / ".DS_Store").write_text("ignore", encoding="utf-8")
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "c.pyc").write_text("ignore", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_skips_ignore_files_and_dirs(self):
        files = iter_skill_files(self.root)
        names = sorted(f.name for f in files)
        self.assertEqual(names, ["a.md", "b.md"])


if __name__ == "__main__":
    unittest.main()
