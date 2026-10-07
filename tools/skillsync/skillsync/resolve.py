"""分叉裁决：在 canonical 与某平台实体副本之间二选一，全程先备份。

- --winner canonical：平台实体目录改名为 .bak-<时间戳>，原位换成指向 canonical 的软链。
- --winner platform：以平台副本覆盖 canonical（canonical 旧内容先在 skills/ 内备份），
  随后平台目录同样备份并转软链，保证最终四平台都指向同一份内容。

已软链/不存在的路径不适用本命令；裁决前要求 catalog 已登记该技能且包含目标平台。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core import PLATFORMS, SKILL_MD, SKILLS_DIR, hash_skill, parse_frontmatter
from . import undo as undo_mod
from .store import audit, load_catalog


class ResolveError(Exception):
    pass


@dataclass
class ResolveResult:
    slug: str
    platform: str
    winner: str
    detail: str


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def resolve(platform_key: str, slug: str, winner: str, dry_run: bool = False) -> ResolveResult:
    if platform_key not in PLATFORMS:
        raise ResolveError(f"未知平台：{platform_key}")
    if winner not in ("canonical", "platform"):
        raise ResolveError("winner 必须是 canonical 或 platform")

    catalog = load_catalog()
    meta = catalog.get("skills", {}).get(slug)
    if meta is None:
        raise ResolveError(f"catalog 未登记技能：{slug}（请先 adopt）")
    if platform_key not in (meta.get("platforms") or []):
        raise ResolveError(f"catalog 中 {slug} 的 platforms 不包含 {platform_key}")

    canonical = SKILLS_DIR / slug
    target = PLATFORMS[platform_key].skills_dir / slug
    if not (canonical / SKILL_MD).is_file():
        raise ResolveError(f"canonical 目录缺少 {SKILL_MD}")
    if target.is_symlink():
        raise ResolveError("目标已是软链，不属于分叉裁决场景（如失效请用 apply 修复）")
    if not target.is_dir() or not (target / SKILL_MD).is_file():
        raise ResolveError("目标不是含 SKILL.md 的实体目录，无需裁决")

    if hash_skill(target) == hash_skill(canonical):
        raise ResolveError("两侧哈希一致，不是分叉；用 apply --link 转软链即可")

    if dry_run:
        action = "平台副本备份后换软链（canonical 胜出）" if winner == "canonical" \
            else "平台副本覆盖 canonical，随后平台目录备份并换软链（platform 胜出）"
        return ResolveResult(slug, platform_key, winner, f"预演：{action}")

    target.parent.mkdir(parents=True, exist_ok=True)
    tx = undo_mod.Tx("resolve", slug)
    if winner == "canonical":
        backup = target.with_name(target.name + ".bak-" + _stamp())
        tx.unbak(backup, target)  # 撤销 = 删软链 + 平台实体目录改回原名
        target.rename(backup)
        os.symlink(canonical, target)
        detail = f"平台目录已备份为 {backup.name}，原位换成软链（canonical 胜出）"
    else:
        canonical_backup = canonical.with_name(canonical.name + ".canonical.bak-" + _stamp())
        # 撤销只需一条 restore_bak_dir（内部先清目标再改回原名），勿叠加 rm_tree
        tx.restore_bak_dir(canonical_backup, canonical)
        shutil.copytree(canonical, canonical_backup)
        # 用平台内容重建 canonical
        shutil.rmtree(canonical)
        shutil.copytree(target, canonical, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        fm, err = parse_frontmatter(canonical)
        if fm is None or str((fm or {}).get("name") or "").strip() != slug:
            raise ResolveError(
                f"平台副本 frontmatter 不合规（{err or 'name 不匹配'}），"
                f"已保留旧 canonical 备份 {canonical_backup.name}，请人工处理"
            )
        backup = target.with_name(target.name + ".bak-" + _stamp())
        tx.unbak(backup, target)
        target.rename(backup)
        os.symlink(canonical, target)
        detail = (f"canonical 旧版备份为 {canonical_backup.name}，"
                  f"已采用平台内容；平台目录备份为 {backup.name} 并换成软链")

    audit("resolve", slug=slug, platform=platform_key, winner=winner,
          detail=detail, undo=tx.id)
    tx.commit(detail)
    return ResolveResult(slug, platform_key, winner, detail)
