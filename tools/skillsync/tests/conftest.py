"""pytest 共享配置：让测试无需安装即可 import skillsync 包。"""
import sys
from pathlib import Path

# 将 tools/skillsync/ 加入 sys.path，使 `import skillsync` 可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
