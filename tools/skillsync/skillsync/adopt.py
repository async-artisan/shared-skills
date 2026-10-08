"""采纳：把平台目录中的实体技能复制进 canonical 仓并登记 catalog。

安全原则：
- 只复制，不删除、不改动源目录；--link 时源目录先备份再换成软链；
- canonical 已存在且内容不同即冲突，默认中止，--conflict=diff 输出差异后仍中止，
  分叉裁决权留给人；
- 采纳前跑静态安全扫描，WARN 仅提示不阻断。
"""
from __future__ import annotations

import difflib
import filecmp
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import apply as apply_mod
from . import undo as undo_mod
from .core import PLATFORMS, SKILL_MD, SKILLS_DIR, hash_skill, parse_frontmatter, scan_platform
from .security import scan_skill
from .store import audit, load_catalog, save_catalog


class AdoptError(Exception):
    pass


@dataclass
class AdoptResult:
    slug: str
    source_platform: str
    status: str          # adopted / exists-same / conflict / error
    detail: str = ""
    security_warnings: int = 0


def _find_source(platform_key: str, name: str) -> Path:
    platform = PLATFORMS[platform_key]
    entries, _ = scan_platform(platform)
    for e in entries:
        if e.name == name and not e.is_symlink:
            return e.path
    raise AdoptError(f"在 {platform.label} 目录中找不到实体技能目录：{name}（软链/已纳管的不需要采纳）")


def adopt(platform_key: str, name: str, target_platforms: list[str] | None = None,
          link: bool = False, conflict: str = "skip", slug: str | None = None,
          source_path: str | Path | None = None,
          tx: "undo_mod.Tx | None" = None) -> AdoptResult:
    if platform_key not in PLATFORMS:
        raise AdoptError(f"未知平台：{platform_key}")
    if source_path is not None:
        source = Path(source_path).expanduser().resolve()
        if not (source / SKILL_MD).is_file():
            raise AdoptError(f"--from-path 不是含 SKILL.md 的技能目录：{source}")
    else:
        source = _find_source(platform_key, name)

    fm, err = parse_frontmatter(source)
    if fm is None:
        raise AdoptError(f"{name} 的 frontmatter 不合规：{err}")
    fm_name = str(fm.get("name") or "").strip()
    if not fm_name:
        raise AdoptError(f"{name} 的 SKILL.md frontmatter 缺少 name")
    slug = slug or fm_name
    if slug != fm_name:
        raise AdoptError(
            f"frontmatter name（{fm_name}）与目标 slug（{slug}）不一致；"
            "本工具不擅自改写技能内容，请先人工统一后再采纳"
        )
    if not all(c.isalnum() or c == "-" for c in slug) or " " in slug:
        raise AdoptError(f"技能名不符合 kebab-case 规范（小写字母/数字/连字符）：{slug}")

    dest = SKILLS_DIR / slug
    source_digest = hash_skill(source)

    if dest.exists():
        dest_digest = hash_skill(dest)
        if dest_digest == source_digest:
            return AdoptResult(slug, platform_key, "exists-same",
                               "canonical 已存在且内容完全一致，无需采纳")
        if conflict == "diff":
            diff = _diff_dirs(source, dest)
            raise AdoptError("存在分叉，已生成差异（仍需人工裁决）：\n" + diff)
        raise AdoptError("canonical 已存在同名但内容不同的技能（分叉）；"
                         "使用 --conflict=diff 查看差异，人工合并后再采纳")

    warnings = scan_skill(source)
    warn_n = sum(1 for w in warnings if w["level"] == "WARN")
    for w in warnings:
        print(f"  [{w['level']}] {w['file']}: {w['rule']} -> {w['snippet']}")

    # 复制新增（绝不改动源目录）；先记录撤销指令再落盘
    owned = tx or undo_mod.Tx("adopt", slug)
    owned.rm_tree(dest)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # 落盘复核
        if not (dest / SKILL_MD).is_file() or hash_skill(dest) != source_digest:
            raise AdoptError("复制后校验失败：目标目录与源目录哈希不一致，请检查后重试")

        platforms = target_platforms or [platform_key]
        unknown = [p for p in platforms if p not in PLATFORMS]
        if unknown:
            raise AdoptError(f"目标平台未知：{', '.join(unknown)}")

        catalog = load_catalog()
        catalog["skills"][slug] = {
            "source_platform": platform_key,
            "platforms": platforms,
            "tools_required": [],
            "depends_on": [],
            "description_zh": "",
            "adopted_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        owned.catalog_set(slug, None)
        save_catalog(catalog)
        audit("adopt", slug=slug, platform=platform_key, targets=platforms,
              security_warnings=warn_n, source=str(source), undo=owned.id)

        detail = "已复制进 canonical 并登记 catalog"
        if link:
            actions = apply_mod.run(write=True, only=slug, link=True, tx=owned)
            changed = [f"{a.platform}:{a.kind}" for a in actions if a.kind not in ("ok", "refused")]
            detail += "；软链下发：" + (", ".join(changed) if changed else "无变更")
    except Exception:
        # 中途失败：立即 commit Tx 确保可撤销，而非丢弃记录留下孤儿
        if tx is None:
            owned.commit(f"adopt 失败回滚点：{slug}（部分落盘，可通过 undo 恢复）")
        raise
    if tx is None:
        owned.commit(detail)
    return AdoptResult(slug, platform_key, "adopted", detail, warn_n)


def _diff_dirs(source: Path, dest: Path) -> str:
    cmp = filecmp.dircmp(source, dest)
    lines = [f"仅平台侧有：{cmp.left_only}" if cmp.left_only else "",
             f"仅仓内有：{cmp.right_only}" if cmp.right_only else ""]
    out = [ln for ln in lines if ln]
    name = SKILL_MD
    if (source / name).is_file() and (dest / name).is_file():
        a = (source / name).read_text(encoding="utf-8", errors="replace").splitlines()
        b = (dest / name).read_text(encoding="utf-8", errors="replace").splitlines()
        out.extend(difflib.unified_diff(b, a, fromfile=f"canonical/{name}",
                                        tofile=f"platform/{name}", lineterm=""))
    return "\n".join(out) or "（文件级差异，请人工对比目录）"
