"""canonical 仓自检：frontmatter 规范、catalog 一致性、依赖与本机工具、脚本风险。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

from .core import PLATFORMS, SKILL_MD, SKILLS_DIR, parse_frontmatter
from .security import scan_skill
from .store import load_catalog


@dataclass
class Check:
    scope: str       # skill / catalog
    name: str
    level: str       # FAIL / WARN
    message: str


def run() -> list[Check]:
    checks: list[Check] = []
    catalog = load_catalog()
    catalog_skills = catalog.get("skills", {})

    canonical_names: set[str] = set()
    if SKILLS_DIR.exists():
        for child in sorted(SKILLS_DIR.iterdir()):
            if ".bak" in child.name:
                continue
            if not child.is_dir() or not (child / SKILL_MD).is_file():
                continue
            canonical_names.add(child.name)
            fm, err = parse_frontmatter(child)
            if fm is None:
                checks.append(Check("skill", child.name, "FAIL", err or "frontmatter 无效"))
                continue
            if str(fm.get("name") or "").strip() != child.name:
                checks.append(Check("skill", child.name, "FAIL",
                                    f"frontmatter name（{fm.get('name')}）与目录名（{child.name}）不一致"))
            if not str(fm.get("description") or "").strip():
                checks.append(Check("skill", child.name, "FAIL", "缺少 description"))
            if child.name not in catalog_skills:
                checks.append(Check("skill", child.name, "WARN", "skills/ 中存在但未登记 catalog.yaml"))
            for w in scan_skill(child):
                if w["level"] == "WARN":
                    checks.append(Check("skill", child.name, "WARN",
                                        f"{w['file']}：{w['rule']}"))

    for slug, meta in catalog_skills.items():
        if slug not in canonical_names:
            checks.append(Check("catalog", slug, "FAIL", "catalog 有条目但 skills/ 缺目录"))
            continue
        unknown = [p for p in (meta.get("platforms") or []) if p not in PLATFORMS]
        if unknown:
            checks.append(Check("catalog", slug, "FAIL", f"未知平台：{', '.join(unknown)}"))
        for dep in meta.get("depends_on") or []:
            if dep not in canonical_names:
                checks.append(Check("catalog", slug, "FAIL", f"依赖技能不存在：{dep}"))
        for tool in meta.get("tools_required") or []:
            if shutil.which(tool) is None:
                checks.append(Check("catalog", slug, "WARN", f"本机缺少声明依赖的命令：{tool}"))

    return checks
