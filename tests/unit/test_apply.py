"""apply.py 写操作的隔离回归测试。"""
from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

import _bootstrap  # noqa: F401 设置隔离的 SKILLSYNC_HOME
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

from skillsync import apply as apply_mod  # noqa: E402
from skillsync import undo as undo_mod  # noqa: E402
from skillsync.core import AUDIT_PATH, PLATFORMS, REGISTRY_DIR, SKILLS_DIR  # noqa: E402
from skillsync.platforms import Platform  # noqa: E402
from skillsync.store import load_catalog, load_state, save_catalog, save_state  # noqa: E402


class TestApply(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SKILLS_DIR, ignore_errors=True)
        SKILLS_DIR.mkdir(parents=True)
        self.platform_dir = SKILLS_DIR.parent / "platform-test" / "skills"
        shutil.rmtree(self.platform_dir.parent, ignore_errors=True)
        self.platform_dir.mkdir(parents=True)
        self.platform_patch = mock.patch.dict(
            PLATFORMS,
            {"codex": Platform("codex", "Codex test", self.platform_dir)},
        )
        self.platform_patch.start()
        self.addCleanup(self.platform_patch.stop)

        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        save_catalog({"version": 1, "skills": {}})
        save_state({"version": 1, "ignored": [], "last_apply": {}})
        if AUDIT_PATH.exists():
            AUDIT_PATH.unlink()
        undo_dir = REGISTRY_DIR / "undo"
        if undo_dir.exists():
            shutil.rmtree(undo_dir)

    def _add_skill(self, slug: str, description: str = "test") -> Path:
        skill = SKILLS_DIR / slug
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: {description}\n---\n# {slug}\n",
            encoding="utf-8",
        )
        catalog = load_catalog()
        catalog["skills"][slug] = {
            "source_platform": "codex",
            "platforms": ["codex"],
        }
        save_catalog(catalog)
        return skill

    def test_write_creates_link_and_undo_record(self):
        canonical = self._add_skill("alpha")

        actions = apply_mod.run(write=True)

        target = self.platform_dir / "alpha"
        self.assertEqual([action.kind for action in actions], ["link-create"])
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), canonical.resolve())
        self.assertEqual(load_state()["last_apply"]["changed"], 1)
        self.assertTrue(AUDIT_PATH.exists())
        undo_files = list((REGISTRY_DIR / "undo").glob("*-apply.json"))
        self.assertEqual(len(undo_files), 1)
        self.assertEqual(json.loads(undo_files[0].read_text())["ops"][0]["op"], "rm_link")

    def test_dry_run_does_not_write_or_record_audit(self):
        self._add_skill("alpha")

        actions = apply_mod.run(write=False)

        self.assertEqual([action.kind for action in actions], ["link-create"])
        self.assertFalse((self.platform_dir / "alpha").exists())
        self.assertFalse((self.platform_dir / "alpha").is_symlink())
        self.assertEqual(load_state()["last_apply"], {})
        self.assertFalse(AUDIT_PATH.exists())
        self.assertFalse((REGISTRY_DIR / "undo").exists())

    def test_forked_directory_is_refused_without_overwrite(self):
        self._add_skill("alpha", "canonical")
        target = self.platform_dir / "alpha"
        target.mkdir()
        original = "---\nname: alpha\ndescription: platform copy\n---\n# platform\n"
        (target / "SKILL.md").write_text(original, encoding="utf-8")

        actions = apply_mod.run(write=True)

        self.assertEqual([action.kind for action in actions], ["refused"])
        self.assertFalse(target.is_symlink())
        self.assertEqual((target / "SKILL.md").read_text(encoding="utf-8"), original)

    def test_force_replaces_foreign_symlink(self):
        canonical = self._add_skill("alpha")
        foreign = self.platform_dir.parent / "foreign"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("foreign", encoding="utf-8")
        target = self.platform_dir / "alpha"
        target.symlink_to(foreign, target_is_directory=True)

        actions = apply_mod.run(write=True, force=True)

        self.assertEqual([action.kind for action in actions], ["relink-foreign"])
        self.assertEqual(target.resolve(), canonical.resolve())
        self.assertTrue(foreign.exists())
        undo_file = next((REGISTRY_DIR / "undo").glob("*-apply.json"))
        undo_mod.execute(undo_file.stem)
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), foreign.resolve())

    def test_mid_operation_failure_rolls_back_completed_links(self):
        self._add_skill("alpha")
        self._add_skill("beta")
        real_symlink = os.symlink
        calls = 0

        def fail_second_link(source, target, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected symlink failure")
            return real_symlink(source, target, *args, **kwargs)

        with mock.patch.object(apply_mod.os, "symlink", side_effect=fail_second_link):
            with self.assertRaises(apply_mod.ApplyError):
                apply_mod.run(write=True)

        self.assertFalse((self.platform_dir / "alpha").is_symlink())
        self.assertFalse((self.platform_dir / "beta").is_symlink())
        self.assertEqual(load_state()["last_apply"], {})
        self.assertFalse(AUDIT_PATH.exists())
        self.assertFalse((REGISTRY_DIR / "undo").exists())

    def test_rollback_failure_keeps_retryable_undo_record(self):
        self._add_skill("alpha")
        self._add_skill("beta")
        real_symlink = os.symlink
        calls = 0

        def fail_second_link(source, target, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected symlink failure")
            return real_symlink(source, target, *args, **kwargs)

        original_apply_op = undo_mod._apply_op

        def fail_rollback(op):
            if op["op"] == "rm_link":
                raise OSError("injected rollback failure")
            return original_apply_op(op)

        with mock.patch.object(apply_mod.os, "symlink", side_effect=fail_second_link), \
             mock.patch.object(undo_mod, "_apply_op", side_effect=fail_rollback):
            with self.assertRaises(apply_mod.ApplyError) as raised:
                apply_mod.run(write=True)

        uid = raised.exception.undo_id
        self.assertTrue(uid)
        record_path = REGISTRY_DIR / "undo" / f"{uid}.json"
        self.assertTrue(record_path.is_file())
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertFalse(record["executed"])
        self.assertIn("回滚失败", record["detail"])
        self.assertFalse(AUDIT_PATH.exists())

        undo_mod.execute(uid)
        self.assertFalse((self.platform_dir / "alpha").is_symlink())

    def test_rollback_type_change_keeps_retryable_undo_record(self):
        self._add_skill("alpha")
        self._add_skill("beta")
        real_symlink = os.symlink
        calls = 0

        def fail_second_link(source, target, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                first = self.platform_dir / "alpha"
                first.unlink()
                first.mkdir()
                (first / "marker").write_text("preserve", encoding="utf-8")
                raise OSError("injected symlink failure")
            return real_symlink(source, target, *args, **kwargs)

        with mock.patch.object(apply_mod.os, "symlink", side_effect=fail_second_link):
            with self.assertRaises(apply_mod.ApplyError) as raised:
                apply_mod.run(write=True)

        self.assertTrue(raised.exception.undo_id)
        self.assertTrue((self.platform_dir / "alpha" / "marker").is_file())
        record_path = REGISTRY_DIR / "undo" / f"{raised.exception.undo_id}.json"
        self.assertTrue(record_path.is_file())
        self.assertFalse(json.loads(record_path.read_text()) ["executed"])

    def test_audit_failure_does_not_turn_successful_apply_into_failed_response(self):
        self._add_skill("alpha")

        with mock.patch.object(apply_mod, "audit", side_effect=OSError("disk full")):
            actions = apply_mod.run(write=True)

        self.assertTrue((self.platform_dir / "alpha").is_symlink())
        self.assertTrue(any(action.kind == "warning" for action in actions))
        self.assertEqual(len(list((REGISTRY_DIR / "undo").glob("*-apply.json"))), 1)

    def test_shared_transaction_is_not_committed_or_rolled_back_twice(self):
        self._add_skill("alpha")
        self._add_skill("beta")
        tx = undo_mod.Tx("adopt", "alpha")
        tx.catalog_set("prior-operation", None)
        original_ops = list(tx.ops)
        real_symlink = os.symlink
        calls = 0

        def fail_second_link(source, target, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected symlink failure")
            return real_symlink(source, target, *args, **kwargs)

        with mock.patch.object(apply_mod.os, "symlink", side_effect=fail_second_link):
            with self.assertRaises(apply_mod.ApplyError):
                apply_mod.run(write=True, tx=tx)

        self.assertEqual(tx.ops, original_ops)
        self.assertFalse((REGISTRY_DIR / "undo").exists())
        self.assertFalse((self.platform_dir / "alpha").is_symlink())

    def test_preflight_rejects_stale_plan_before_any_write(self):
        self._add_skill("alpha")
        self._add_skill("beta")
        original_plan = apply_mod._plan

        def plan_then_create_conflict(*args, **kwargs):
            actions = original_plan(*args, **kwargs)
            target = self.platform_dir / "beta"
            target.mkdir()
            (target / "SKILL.md").write_text("foreign", encoding="utf-8")
            return actions

        with mock.patch.object(apply_mod, "_plan", side_effect=plan_then_create_conflict):
            with self.assertRaises(apply_mod.ApplyError):
                apply_mod.run(write=True)

        self.assertFalse((self.platform_dir / "alpha").is_symlink())
        self.assertTrue((self.platform_dir / "beta").is_dir())


if __name__ == "__main__":
    unittest.main()
