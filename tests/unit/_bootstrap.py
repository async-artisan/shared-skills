"""测试隔离 bootstrap：在 import skillsync 之前设置 SKILLSYNC_HOME。

unittest discover 不执行 __init__.py（当 -s 指向的目录作为顶层扫描起点时），
所以环境变量必须由每个 test_*.py 在 import skillsync 之前主动 import 本模块来设置。

core.py 的 REPO_ROOT 是模块级常量，第一次 import 时根据 SKILLSYNC_HOME 计算。
只要第一个被加载的 test_*.py 先 import _bootstrap，REPO_ROOT 就会锁定到
隔离临时目录，后续 test_doctor.py 的 shutil.rmtree(SKILLS_DIR) 不会清空真实仓。
"""
import os
import tempfile

if "SKILLSYNC_HOME" not in os.environ:
    _TMP = tempfile.TemporaryDirectory(prefix="skillsync-unit-")
    os.environ["SKILLSYNC_HOME"] = _TMP.name
