"""tests.unit 包入口：在 import core 之前锁定 SKILLSYNC_HOME 到隔离临时目录。

为什么放这里：core.py 的 REPO_ROOT 是模块级常量，第一次 import 时根据
SKILLSYNC_HOME 计算。unittest discover 按字母序加载测试模块，第一个被
import 的模块（通常是 test_core）会触发 core 加载。如果此时
SKILLSYNC_HOME 未设置，REPO_ROOT 会锁定到真实仓库，后续测试的
shutil.rmtree(SKILLS_DIR) 会清空真实 skills/ 目录。

本 __init__.py 在包加载时（早于任何测试模块的 import）设置 SKILLSYNC_HOME
到本进程专用的临时目录，并在模块卸载时清理。
"""
import os
import tempfile

_UNIT_TMP = tempfile.TemporaryDirectory(prefix="skillsync-unit-")
os.environ["SKILLSYNC_HOME"] = _UNIT_TMP.name
