"""skillsync：跨平台 Agent Skill 单仓同步工具。

只依赖 Python 标准库。本机事实源为仓库根目录下的 skills/（私有技能库）
与 registry/catalog.yaml（私有台账，首次运行从 catalog.example.yaml 生成），
二者均不随公开仓分发；公开仓只包含工具代码与仓骨架。
"""

__version__ = "0.1.0"
