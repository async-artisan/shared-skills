"""store.py 与 undo.py 单元测试：catalog/state 读写、审计、撤销指令。"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

# SKILLSYNC_HOME 由 tests/unit/__init__.py 统一设置，确保 core.REPO_ROOT
# 在首次 import 时就锁定到隔离临时目录，避免污染真实仓
from skillsync import core as core_mod  # noqa: E402
from skillsync.core import (  # noqa: E402
    AUDIT_PATH, CATALOG_PATH, REGISTRY_DIR, SKILLS_DIR, STATE_PATH,
)
from skillsync.store import (  # noqa: E402
    audit, load_catalog, load_state, save_catalog, save_state,
)
from skillsync import undo as undo_mod  # noqa: E402


class TestStore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        save_catalog({"version": 1, "skills": {}})
        save_state({"version": 1, "ignored": [], "last_apply": {}})
        if AUDIT_PATH.exists():
            AUDIT_PATH.unlink()

    def test_load_empty_catalog(self):
        data = load_catalog()
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["skills"], {})

    def test_save_and_load_catalog_roundtrip(self):
        data = {
            "version": 1,
            "skills": {
                "alpha": {
                    "source_platform": "codex",
                    "platforms": ["codex", "trae"],
                    "tools_required": [],
                    "depends_on": [],
                    "description_zh": "测试",
                    "adopted_at": "2026-10-07T09:00:00+08:00",
                    "archived": True,
                    "archived_at": "2026-10-07T09:00:00+08:00",
                },
            },
        }
        save_catalog(data)
        loaded = load_catalog()
        self.assertEqual(loaded, data)
        self.assertTrue(loaded["skills"]["alpha"]["archived"])

    def test_load_empty_state(self):
        state = load_state()
        self.assertEqual(state["ignored"], [])
        self.assertEqual(state["last_apply"], {})

    def test_save_and_load_state_roundtrip(self):
        state = {
            "version": 1,
            "ignored": ["codex/foo", "trae/bar"],
            "last_apply": {"time": "2026-10-07", "changed": 5},
        }
        save_state(state)
        self.assertEqual(load_state(), state)


class TestAudit(unittest.TestCase):
    def setUp(self):
        if AUDIT_PATH.exists():
            AUDIT_PATH.unlink()

    def test_audit_writes_jsonl(self):
        audit("test", slug="alpha", platform="codex", via="unit-test",
              extra="info")
        lines = AUDIT_PATH.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["action"], "test")
        self.assertEqual(rec["slug"], "alpha")
        self.assertEqual(rec["platform"], "codex")
        self.assertEqual(rec["via"], "unit-test")
        self.assertEqual(rec["extra"], "info")
        self.assertIn("time", rec)

    def test_audit_multiple_appends(self):
        audit("first")
        audit("second")
        lines = AUDIT_PATH.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["action"], "first")
        self.assertEqual(json.loads(lines[1])["action"], "second")


class TestUndo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        save_catalog({"version": 1, "skills": {}})

    def test_tx_collects_ops_without_writing(self):
        tx = undo_mod.Tx("test-action", "alpha")
        tx.catalog_set("alpha", {"source_platform": "codex"})
        undo_dir = SKILLS_DIR.parent / "registry" / "undo"
        if undo_dir.exists():
            for f in undo_dir.glob("*.json"):
                f.unlink()
        uid = tx.commit("测试提交")
        path = undo_dir / f"{uid}.json"
        self.assertTrue(path.exists())
        rec = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(rec["action"], "test-action")
        self.assertEqual(rec["slug"], "alpha")
        self.assertFalse(rec["executed"])
        self.assertEqual(len(rec["ops"]), 1)
        self.assertEqual(rec["ops"][0]["op"], "catalog_set")

    def test_tx_discard_no_file(self):
        tx = undo_mod.Tx("discard-test", "alpha")
        tx.catalog_set("alpha", {"x": 1})
        tx.discard()
        undo_dir = SKILLS_DIR.parent / "registry" / "undo"
        if undo_dir.exists():
            files = list(undo_dir.glob("*discard-test*.json"))
            self.assertEqual(files, [])

    def test_execute_catalog_set_restores_entry(self):
        import copy
        original_entry = {"source_platform": "codex", "platforms": ["codex"]}
        save_catalog({"version": 1, "skills": {"alpha": original_entry}})
        tx = undo_mod.Tx("archive", "alpha")
        tx.catalog_set("alpha", copy.deepcopy(original_entry))
        uid = tx.commit("归档 alpha")
        cat = load_catalog()
        cat["skills"]["alpha"]["archived"] = True
        save_catalog(cat)
        result = undo_mod.execute(uid)
        self.assertTrue(result["ok"])
        restored = load_catalog()
        self.assertNotIn("archived", restored["skills"]["alpha"])
        self.assertEqual(restored["skills"]["alpha"], original_entry)

    def test_execute_catalog_set_removes_entry_when_none(self):
        save_catalog({"version": 1, "skills": {"alpha": {"x": 1}}})
        tx = undo_mod.Tx("adopt", "alpha")
        tx.catalog_set("alpha", None)
        uid = tx.commit("采纳 alpha")
        cat = load_catalog()
        cat["skills"]["alpha"] = {"x": 1, "y": 2}
        save_catalog(cat)
        undo_mod.execute(uid)
        restored = load_catalog()
        self.assertNotIn("alpha", restored["skills"])

    def test_execute_idempotent(self):
        tx = undo_mod.Tx("once", "alpha")
        tx.catalog_set("alpha", {"x": 1})
        uid = tx.commit("once")
        undo_mod.execute(uid)
        with self.assertRaises(undo_mod.UndoError):
            undo_mod.execute(uid)

    def test_execute_unknown_op_raises(self):
        tx = undo_mod.Tx("bad", "alpha")
        tx.ops.append({"op": "nonexistent_op", "slug": "alpha"})
        uid = tx.commit("bad")
        with self.assertRaises(undo_mod.UndoError):
            undo_mod.execute(uid)

    def test_execute_unknown_id_raises(self):
        with self.assertRaises(undo_mod.UndoError):
            undo_mod.execute("nonexistent-id-12345")

    def test_state_ignored_op(self):
        save_state({"version": 1, "ignored": [], "last_apply": {}})
        tx = undo_mod.Tx("ignore", "alpha")
        tx.state_ignored("codex/alpha", add=True)
        uid = tx.commit("忽略 alpha")
        state = load_state()
        state["ignored"] = ["other/thing"]
        save_state(state)
        undo_mod.execute(uid)
        restored = load_state()
        self.assertIn("codex/alpha", restored["ignored"])


def tearDownModule():
    pass


if __name__ == "__main__":
    unittest.main()
