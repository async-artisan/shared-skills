"""pytest 共享配置：导入包路径，并隔离仓库与平台目录。"""
import os
import sys
import tempfile
from pathlib import Path

# 将 tools/skillsync/ 加入 sys.path，使 `import skillsync` 可用
_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

# 测试始终使用临时仓和临时平台目录，避免读取或写入个人环境。
_TEST_HOME = tempfile.TemporaryDirectory(prefix="skillsync-pytest-")
_ROOT = Path(_TEST_HOME.name)
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
