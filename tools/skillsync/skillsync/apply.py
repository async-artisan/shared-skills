"""下发：把 canonical 技能以软链方式铺到 catalog 声明的各平台目录。

安全原则：只增不删。真实目录永不删除，替换一律先改名为 .bak-<时间戳>；
默认 dry-run，必须显式 --write 才落盘。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import PLATFORMS, SKILL_MD, SKILLS_DIR, hash_skill
from . import undo as undo_mod
from .store import audit, load_catalog, load_state, save_state


def _is_within(child: Path, base: Path) -> bool:
    """判断 child 解析后是否仍在 base 目录内，防止路径穿越。"""
    try:
        return child.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


@dataclass
class Action:
    platform: str
    slug: str = ""
    kind: str = ""        # link-create / copy-to-link / relink-broken / relink-foreign / ok / refused
    detail: str = ""


def _plan(platform_filter: set[str] | None, only: str | None,
          link_copies: bool, force: bool) -> list[Action]:
    catalog = load_catalog()
    actions: list[Action] = []
    for slug, meta in sorted(catalog.get("skills", {}).items()):
        if only and slug != only:
            continue
        targets = set(meta.get("platforms") or [])
        if platform_filter:
            targets &= platform_filter
        canonical = SKILLS_DIR / slug
        if not _is_within(canonical, SKILLS_DIR):
            for p in targets:
                actions.append(Action(p, slug, "refused", f"slug 解析后跳出 skills/ 边界：{slug}"))
            continue
        if not (canonical / SKILL_MD).is_file():
            for p in targets:
                actions.append(Action(p, slug, "refused", "canonical 目录缺少 SKILL.md，跳过"))
            continue
        canonical_digest = hash_skill(canonical)
        for key in sorted(targets):
            platform = PLATFORMS.get(key)
            if platform is None:
                actions.append(Action(key, slug, "refused", f"未知平台 {key}"))
                continue
            target = platform.skills_dir / slug
            actions.append(_plan_one(key, slug, target, canonical_digest, link_copies, force))
    return actions


def _plan_one(key: str, slug: str, target: Path, canonical_digest: str,
              link_copies: bool, force: bool) -> Action:
    if not target.exists() and not target.is_symlink():
        return Action(key, slug, "link-create", f"创建软链 → {target}")
    if target.is_symlink():
        if target.exists() and hash_skill(target) == canonical_digest:
            return Action(key, slug, "ok", "已是正确软链")
        if not target.exists():
            return Action(key, slug, "relink-broken", f"修复失效软链（原目标 {_readlink(target)}）")
        if force:
            return Action(key, slug, "relink-foreign",
                          f"替换仓外软链（原目标 {_readlink(target)}，--force）")
        return Action(key, slug, "refused",
                      f"软链指向仓外：{_readlink(target)}（加 --force 才替换）")
    # 真实目录
    if target.is_dir():
        digest = hash_skill(target)
        if digest == canonical_digest:
            if link_copies:
                return Action(key, slug, "copy-to-link",
                              "内容一致，备份原目录后替换为软链")
            return Action(key, slug, "ok", "内容一致的实体副本（加 --link 可转软链）")
        return Action(key, slug, "refused", "实体目录内容与 canonical 不一致（分叉），"
                                            "请先用 doctor/adopt 裁决，拒绝覆盖")
    return Action(key, slug, "refused", "目标路径存在但既不是目录也不是软链")


def _readlink(path: Path) -> str:
    try:
        return str(path.readlink())
    except OSError:
        return "?"


def run(write: bool = False, platform: str | None = None, only: str | None = None,
        link: bool = False, force: bool = False,
        tx: "undo_mod.Tx | None" = None) -> list[Action]:
    pf = {platform} if platform else None
    actions = _plan(pf, only, link_copies=link, force=force)
    if write:
        owned = tx or undo_mod.Tx("apply")
        catalog = load_catalog()
        for a in actions:
            _execute(a, owned)
        save_state(_touch_last_apply(actions))
        changed = sum(1 for a in actions if a.kind not in ("ok", "refused"))
        audit("apply", platform=platform or "*",
              executed=changed,
              refused=sum(1 for a in actions if a.kind == "refused"), undo=owned.id)
        if tx is None:
            owned.commit(f"下发 {changed} 处变更" + (f"（{only}）" if only else ""))
    return actions


def _execute(a: Action, tx: "undo_mod.Tx") -> None:
    platform = PLATFORMS[a.platform]
    target = platform.skills_dir / a.slug
    canonical = SKILLS_DIR / a.slug
    if a.kind in ("link-create",):
        tx.rm_link(target)  # 撤销 = 移除新建软链
        platform.skills_dir.mkdir(parents=True, exist_ok=True)
        os.symlink(canonical, target)
        a.detail = f"已创建软链 → {canonical}"
    elif a.kind in ("relink-broken", "relink-foreign"):
        tx.restore_link(target, _readlink(target))  # 撤销 = 恢复原指向
        target.unlink()
        os.symlink(canonical, target)
        a.detail = f"已替换为软链 → {canonical}"
    elif a.kind == "copy-to-link":
        backup = target.with_name(target.name + ".bak-" + datetime.now().strftime("%Y%m%d%H%M%S"))
        tx.unbak(backup, target)  # 撤销 = 删软链 + 实体目录改回原名
        target.rename(backup)
        os.symlink(canonical, target)
        a.detail = f"原目录已备份为 {backup.name}，并创建软链"
    # ok / refused 不动作


def _touch_last_apply(actions: list[Action]) -> dict[str, Any]:
    from datetime import datetime, timezone
    state = load_state()
    state["last_apply"] = {
        "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "changed": sum(1 for a in actions if a.kind not in ("ok", "refused")),
        "refused": sum(1 for a in actions if a.kind == "refused"),
    }
    return state
