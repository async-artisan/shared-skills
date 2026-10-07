"""platforms.py 单元测试：内置默认、配置覆盖优先级、路径展开与非法配置报错。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

from skillsync.platforms import (  # noqa: E402
    PlatformConfigError,
    resolve_platforms,
)

CONFIG_1 = """\
version: 1
platforms:
  qwen:
    label: 千问办公
    skills_dir: ~/.qwen-office/skills
  doubao:
    label: 豆包工作
    skills_dir: $MYAPP/skills
"""

CONFIG_2 = """\
version: 1
platforms:
  onlyone:
    label: 唯一
    skills_dir: /opt/onlyone/skills
    builtin_dirnames: [.system, builtin]
"""


class TestDefaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "registry").mkdir(parents=True)
        self.home.mkdir()

    def resolve(self, text=None, env=None):
        return resolve_platforms(
            self.repo / "registry",
            environ=env or {},
            home=self.home,
        )

    def test_no_config_returns_builtin_four(self):
        cfg = self.resolve()
        self.assertEqual(cfg.source, "default")
        self.assertIsNone(cfg.path)
        self.assertEqual(
            list(cfg.platforms.keys()),
            ["codex", "claude", "workbuddy", "trae"],
        )

    def test_default_paths_under_given_home(self):
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["codex"].skills_dir,
            self.home / ".codex" / "skills",
        )
        self.assertEqual(
            cfg.platforms["trae"].skills_dir,
            self.home / ".trae-cn" / "skills",
        )

    def test_default_codex_builtin_dirnames(self):
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["codex"].builtin_dirnames, frozenset({".system"}))
        self.assertEqual(cfg.platforms["trae"].builtin_dirnames, frozenset())

    def test_default_labels_preserved(self):
        cfg = self.resolve()
        self.assertEqual(cfg.platforms["codex"].label, "Codex")
        self.assertEqual(cfg.platforms["workbuddy"].label, "WorkBuddy")


class TestConfigLoading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        self.reg = self.repo / "registry"
        self.reg.mkdir(parents=True)
        self.home.mkdir()

    def resolve(self, env=None):
        return resolve_platforms(
            self.reg, environ=env or {"MYAPP": str(self.home / "myapp")},
            home=self.home)

    def write_repo(self, text):
        (self.reg / "platforms.yaml").write_text(text, encoding="utf-8")

    def write_user(self, text):
        d = self.home / ".skillsync"
        d.mkdir(parents=True, exist_ok=True)
        (d / "platforms.yaml").write_text(text, encoding="utf-8")

    def write_env_file(self, text, env):
        p = Path(self.tmp.name) / "custom-platforms.yaml"
        p.write_text(text, encoding="utf-8")
        env["SKILLSYNC_PLATFORMS"] = str(p)
        return p

    def test_repo_config_replaces_defaults_entirely(self):
        self.write_repo(CONFIG_1)
        cfg = self.resolve()
        self.assertEqual(cfg.source, "repo")
        self.assertEqual(list(cfg.platforms.keys()), ["qwen", "doubao"])
        self.assertNotIn("codex", cfg.platforms)

    def test_user_config_overrides_repo_config(self):
        self.write_repo(CONFIG_1)
        self.write_user(CONFIG_2)
        cfg = self.resolve()
        self.assertEqual(cfg.source, "user")
        self.assertEqual(list(cfg.platforms.keys()), ["onlyone"])

    def test_env_file_overrides_user_and_repo(self):
        self.write_repo(CONFIG_1)
        self.write_user(CONFIG_2)
        env = {"MYAPP": str(self.home / "myapp")}
        self.write_env_file(
            "version: 1\nplatforms:\n  envone:\n"
            "    label: 环境\n    skills_dir: /tmp/envone\n", env)
        cfg = self.resolve(env=env)
        self.assertEqual(cfg.source, "env")
        self.assertEqual(list(cfg.platforms.keys()), ["envone"])

    def test_tilde_expansion(self):
        self.write_repo(CONFIG_1)
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["qwen"].skills_dir,
            self.home / ".qwen-office" / "skills",
        )

    def test_dollar_var_expansion(self):
        self.write_repo(CONFIG_1)
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["doubao"].skills_dir,
            self.home / "myapp" / "skills",
        )

    def test_brace_var_expansion(self):
        self.write_repo(
            "version: 1\nplatforms:\n  x:\n    label: X\n"
            "    skills_dir: ${MYAPP}/x/skills\n")
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["x"].skills_dir,
            self.home / "myapp" / "x" / "skills",
        )

    def test_relative_path_anchored_at_repo_root(self):
        self.write_repo(
            "version: 1\nplatforms:\n  local:\n    label: 本地\n"
            "    skills_dir: vendor/skills\n")
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["local"].skills_dir,
            self.repo / "vendor" / "skills",
        )

    def test_builtin_dirnames_parsed(self):
        self.write_repo(CONFIG_2)
        cfg = self.resolve()
        self.assertEqual(
            cfg.platforms["onlyone"].builtin_dirnames,
            frozenset({".system", "builtin"}),
        )

    def test_label_loaded(self):
        self.write_repo(CONFIG_1)
        cfg = self.resolve()
        self.assertEqual(cfg.platforms["qwen"].label, "千问办公")

    def test_missing_env_file_is_error_not_fallback(self):
        env = {"SKILLSYNC_PLATFORMS": str(self.repo / "nope.yaml")}
        with self.assertRaises(PlatformConfigError):
            self.resolve(env=env)


class TestInvalidConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        self.reg = self.repo / "registry"
        self.reg.mkdir(parents=True)
        self.home.mkdir()

    def bad(self, body: str):
        (self.reg / "platforms.yaml").write_text(body, encoding="utf-8")
        with self.assertRaises(PlatformConfigError):
            resolve_platforms(self.reg, environ={}, home=self.home)

    def test_top_level_not_map(self):
        self.bad("- qwen\n- doubao\n")

    def test_platforms_not_map(self):
        self.bad("version: 1\nplatforms:\n  - qwen\n")

    def test_empty_platforms(self):
        self.bad("version: 1\nplatforms: {}\n")

    def test_bad_key_uppercase(self):
        self.bad("platforms:\n  Qwen:\n    label: q\n    skills_dir: /x\n")

    def test_bad_key_special_chars(self):
        self.bad("platforms:\n  qwen_office:\n    label: q\n    skills_dir: /x\n")

    def test_entry_not_map(self):
        self.bad("platforms:\n  qwen: /opt/x\n")

    def test_missing_label(self):
        self.bad("platforms:\n  qwen:\n    skills_dir: /x\n")

    def test_empty_label(self):
        self.bad("platforms:\n  qwen:\n    label: \"\"\n    skills_dir: /x\n")

    def test_missing_skills_dir(self):
        self.bad("platforms:\n  qwen:\n    label: 千问\n")

    def test_bad_builtin_dirnames(self):
        self.bad(
            "platforms:\n  qwen:\n    label: q\n    skills_dir: /x\n"
            "    builtin_dirnames: ../escape\n")


if __name__ == "__main__":
    unittest.main()
