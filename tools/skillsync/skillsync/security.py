"""轻量安全检查：只做静态模式提示，不联网、不判定恶意。"""
from __future__ import annotations

import re
from pathlib import Path

from .core import iter_skill_files

# (级别, 正则, 说明)
_RULES = [
    ("WARN", re.compile(r"curl[^\n|]*\|\s*(sudo\s+)?(ba|z|fi)?sh"), "远程脚本管道执行（curl|sh）"),
    ("WARN", re.compile(r"wget[^\n|]*\|\s*(sudo\s+)?(ba|z|fi)?sh"), "远程脚本管道执行（wget|sh）"),
    ("WARN", re.compile(r"(\.env|credentials|id_rsa|\.ssh/|tempConfig/)"), "疑似访问凭据/敏感路径"),
    ("INFO", re.compile(r"https?://[^\s'\")]+"), "外联 URL（确认是否为预期端点）"),
]

SCAN_EXTENSIONS = {".sh", ".py", ".js", ".ts", ".mjs", ".php", ".rb", ".md", ".json"}


def scan_skill(skill_dir: Path) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for f in iter_skill_files(skill_dir):
        if f.suffix.lower() not in SCAN_EXTENSIONS:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(skill_dir).as_posix()
        for level, pattern, desc in _RULES:
            for m in pattern.finditer(text):
                findings.append({
                    "level": level,
                    "file": rel,
                    "rule": desc,
                    "snippet": m.group(0)[:80].replace("\n", " "),
                })
    return findings
