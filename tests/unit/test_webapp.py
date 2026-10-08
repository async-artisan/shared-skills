"""webapp.py 单元与接口契约测试：变化签名与翻译引擎缺失降级。

锁住两条容易回归的行为：
1. 只改 SKILL.md / references 里的文件内容（不动任何目录 mtime）也必须改变
   /api/poll 的签名，否则控制台不会自动重扫；
2. 离线翻译引擎缺失时错误必须带 no-translate-engine，前端才能静默降级。

远程导入确认的边界（预览绑定、逐文件摘要、frontmatter 名称一致）见 test_remote_web.py。
"""
from __future__ import annotations

import json
import shutil
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

from skillsync import webapp  # noqa: E402
from skillsync.core import REGISTRY_DIR, SKILL_MD, SKILLS_DIR  # noqa: E402
from skillsync.store import save_catalog, save_state  # noqa: E402
from skillsync.webapp import ApiError, _error_payload, _scan_signature  # noqa: E402


def _skill_md(name: str, description: str = "测试技能") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n"


class TestScanSignature(unittest.TestCase):
    """变化签名必须覆盖技能目录内的文件内容，而不只是目录 mtime。"""

    def setUp(self):
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self.skill = SKILLS_DIR / "sig-demo"
        shutil.rmtree(self.skill, ignore_errors=True)
        self.skill.mkdir(parents=True)
        (self.skill / SKILL_MD).write_text(_skill_md("sig-demo"), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.skill, ignore_errors=True)

    def test_skill_md_content_edit_changes_signature(self):
        before = _scan_signature()
        p = self.skill / SKILL_MD
        p.write_text(p.read_text(encoding="utf-8") + "\n新增说明一行\n", encoding="utf-8")
        self.assertNotEqual(before, _scan_signature())

    def test_nested_file_edit_changes_signature(self):
        refs = self.skill / "references"
        refs.mkdir()
        doc = refs / "guide.md"
        doc.write_text("第一版", encoding="utf-8")
        before = _scan_signature()
        doc.write_text("第二版内容更长", encoding="utf-8")
        self.assertNotEqual(before, _scan_signature())

    def test_unchanged_tree_is_stable(self):
        self.assertEqual(_scan_signature(), _scan_signature())


class TestTranslateEngineMissing(unittest.TestCase):
    """引擎缺失是可预期情况：错误要带机器可读 code，前端才能静默降级。"""

    def test_missing_engine_raises_coded_503(self):
        blocked = {"argostranslate": None,
                   "argostranslate.settings": None,
                   "argostranslate.translate": None}
        with mock.patch.dict(sys.modules, blocked):
            with self.assertRaises(ApiError) as ctx:
                webapp._translate_markdown("A short english sentence for the engine test.")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, "no-translate-engine")

    def test_error_payload_carries_code(self):
        self.assertEqual(
            _error_payload(ApiError("boom", 503, code="no-translate-engine")),
            {"ok": False, "error": "boom", "code": "no-translate-engine"})
        self.assertEqual(_error_payload(ApiError("boom")),
                         {"ok": False, "error": "boom"})


class TestScanPayloadContract(unittest.TestCase):
    """/api/scan 是技能库行源的唯一来源：catalog_slugs 与 descriptions 都必须下发。"""

    @classmethod
    def setUpClass(cls):
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        save_catalog({"version": 1, "skills": {}})
        save_state({"version": 1, "ignored": [], "last_apply": {}})
        cls.httpd, cls.base = webapp.serve(port=0, open_browser=False)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_scan_exposes_library_row_sources(self):
        with urllib.request.urlopen(self.base + "api/scan", timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertIn("catalog_slugs", data)
        self.assertIn("descriptions", data)
        self.assertIn("findings", data)

    def test_registered_skill_without_platforms_is_visible_in_library(self):
        """技能库行源 = catalog 登记的 canonical ∪ 已纳管软链。

        导入的技能不进任何平台，若行源只看 managed-ok，它就在任何页签都不出现。
        """
        slug = "imported-demo"
        skill = SKILLS_DIR / slug
        shutil.rmtree(skill, ignore_errors=True)
        skill.mkdir(parents=True)
        (skill / SKILL_MD).write_text(_skill_md(slug, "远端导入的技能"), encoding="utf-8")
        self.addCleanup(shutil.rmtree, skill, True)
        save_catalog({"version": 1, "skills": {
            slug: {"source_platform": "remote", "platforms": []}}})

        with urllib.request.urlopen(self.base + "api/scan", timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        self.assertIn(slug, data["catalog_slugs"])
        self.assertIn(slug, data["descriptions"])
        self.assertNotIn(slug, [f["name"] for f in data["findings"]
                                if f["status"] == "managed-ok"])


if __name__ == "__main__":
    unittest.main()
