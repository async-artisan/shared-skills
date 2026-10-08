"""测试隔离 bootstrap：在 import skillsync 之前设置临时仓与平台目录。

unittest discover 不执行 __init__.py（当 -s 指向的目录作为顶层扫描起点时），
所以环境变量必须由每个 test_*.py 在 import skillsync 之前主动 import 本模块来设置。

core.py 的 REPO_ROOT 是模块级常量，第一次 import 时根据 SKILLSYNC_HOME 计算。
只要第一个被加载的 test_*.py 先 import _bootstrap，REPO_ROOT 就会锁定到
隔离临时目录，后续 test_doctor.py 的 shutil.rmtree(SKILLS_DIR) 不会清空真实仓。
"""
import os
import tempfile
from pathlib import Path

# 测试始终使用自己的仓和平台目录，即使调用者 shell 已设置这些变量。
_TMP = tempfile.TemporaryDirectory(prefix="skillsync-unit-")
_ROOT = Path(_TMP.name)
os.environ["SKILLSYNC_HOME"] = str(_ROOT)
_PLATFORM_CONFIG = _ROOT / "platforms.yaml"
_PLATFORM_CONFIG.write_text(
    "version: 1\n"
    "platforms:\n"
    "  codex:\n"
    "    label: Codex\n"
    "    skills_dir: ${SKILLSYNC_HOME}/platforms/codex/skills\n"
    "    builtin_dirnames: [.system]\n"
    "  claude:\n"
    "    label: Claude\n"
    "    skills_dir: ${SKILLSYNC_HOME}/platforms/claude/skills\n"
    "  workbuddy:\n"
    "    label: WorkBuddy\n"
    "    skills_dir: ${SKILLSYNC_HOME}/platforms/workbuddy/skills\n"
    "  trae:\n"
    "    label: TRAE\n"
    "    skills_dir: ${SKILLSYNC_HOME}/platforms/trae/skills\n",
    encoding="utf-8",
)
os.environ["SKILLSYNC_PLATFORMS"] = str(_PLATFORM_CONFIG)
for _platform in ("codex", "claude", "workbuddy", "trae"):
    (_ROOT / "platforms" / _platform / "skills").mkdir(parents=True, exist_ok=True)
