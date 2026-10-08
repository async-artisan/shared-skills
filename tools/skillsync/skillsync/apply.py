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

from .core import PLATFORMS, SKILL_MD, SKILLS_DIR, STATE_PATH, hash_skill
from . import undo as undo_mod
from .store import audit, load_catalog, load_state, save_state


class ApplyError(RuntimeError):
    """apply 写入或自动回滚失败。

    ``undo_id`` 只在自动回滚没有完全成功、需要用户重试撤销时设置。
    """

    def __init__(self, message: str, undo_id: str | None = None):
        super().__init__(message)
        self.undo_id = undo_id


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


def _nearest_existing_parent(path: Path) -> Path:
    """返回 path 的最近已存在父目录，不创建任何目录。"""
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _preflight(actions: list[Action]) -> None:
    """在第一个写动作前复核计划，阻止计划与当前文件系统状态脱节。"""
    errors: list[str] = []
    seen_targets: set[Path] = set()
    for action in actions:
        if action.kind in ("ok", "refused"):
            continue
        platform = PLATFORMS.get(action.platform)
        if platform is None:
            errors.append(f"{action.platform}/{action.slug}：平台不存在")
            continue
        target = platform.skills_dir / action.slug
        canonical = SKILLS_DIR / action.slug
        # 只解析父目录，不能解析最终目标：--force 允许替换指向仓外的旧软链。
        try:
            target_parent_ok = target.parent.resolve(strict=False).is_relative_to(
                platform.skills_dir.resolve(strict=False))
        except (OSError, ValueError):
            target_parent_ok = False
        if not target_parent_ok:
            errors.append(f"{action.platform}/{action.slug}：目标路径越界")
            continue
        if Path(action.slug).name != action.slug or action.slug in (".", ".."):
            errors.append(f"{action.platform}/{action.slug}：技能名必须是单层目录名")
            continue
        lexical_target = target.absolute()
        if lexical_target in seen_targets:
            errors.append(f"{action.platform}/{action.slug}：目标路径重复")
        seen_targets.add(lexical_target)
        if not (canonical / SKILL_MD).is_file():
            errors.append(f"{action.platform}/{action.slug}：canonical 缺少 {SKILL_MD}")
            continue
        parent = _nearest_existing_parent(target.parent)
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            errors.append(f"{action.platform}/{action.slug}：目标父目录不可写：{parent}")
            continue
        if action.kind == "link-create":
            if target.exists() or target.is_symlink():
                errors.append(f"{action.platform}/{action.slug}：目标已在计划后发生变化")
        elif action.kind == "relink-broken":
            if not target.is_symlink() or target.exists():
                errors.append(f"{action.platform}/{action.slug}：失效软链已在计划后发生变化")
        elif action.kind == "relink-foreign":
            if not target.is_symlink() or not target.exists():
                errors.append(f"{action.platform}/{action.slug}：仓外软链已在计划后发生变化")
        elif action.kind == "copy-to-link":
            if not target.is_dir() or target.is_symlink():
                errors.append(f"{action.platform}/{action.slug}：实体目录已在计划后发生变化")
            elif hash_skill(target) != hash_skill(canonical):
                errors.append(f"{action.platform}/{action.slug}：内容已在计划后发生变化")
    if errors:
        raise ApplyError("apply 预检失败，未执行任何写操作：" + "；".join(errors))


def _rollback(ops: list[dict[str, Any]]) -> list[str]:
    """逆序回放本次 apply 新增的撤销指令，尽量完成全部回滚。"""
    errors: list[str] = []
    for op in reversed(ops):
        try:
            undo_mod._apply_op(op)
        except Exception as exc:
            errors.append(f"{op.get('op', '?')}: {exc}")
    return errors


def run(write: bool = False, platform: str | None = None, only: str | None = None,
        link: bool = False, force: bool = False,
        tx: "undo_mod.Tx | None" = None) -> list[Action]:
    pf = {platform} if platform else None
    actions = _plan(pf, only, link_copies=link, force=force)
    if write:
        owned = tx or undo_mod.Tx("apply")
        _preflight(actions)
        op_start = len(owned.ops)
        state_before = load_state()
        state_file_existed = STATE_PATH.exists()
        state_write_attempted = False
        commit_attempted = False
        try:
            for a in actions:
                _execute(a, owned)
            state_write_attempted = True
            save_state(_touch_last_apply(actions))
            changed = sum(1 for a in actions if a.kind not in ("ok", "refused"))
            if tx is None:
                commit_attempted = True
                owned.commit(f"下发 {changed} 处变更" + (f"（{only}）" if only else ""))
        except Exception as exc:
            new_ops = owned.ops[op_start:]
            rollback_errors = _rollback(new_ops)
            if state_write_attempted:
                try:
                    if state_file_existed:
                        save_state(state_before)
                    else:
                        STATE_PATH.unlink(missing_ok=True)
                except Exception as state_exc:
                    rollback_errors.append(f"state: {state_exc}")
            if commit_attempted and not rollback_errors:
                undo_path = SKILLS_DIR.parent / "registry" / "undo" / f"{owned.id}.json"
                try:
                    undo_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    rollback_errors.append(f"undo-record cleanup: {cleanup_exc}")
            if not rollback_errors:
                del owned.ops[op_start:]
                raise ApplyError(f"apply 执行失败，已自动回滚：{exc}") from exc
            # 外层 adopt 共享同一 Tx：保留新增 ops，由外层统一提交回滚点。
            detail = (f"apply 执行失败，回滚失败（部分动作已回滚）：{exc}；"
                      f"可通过 undo 重试：{'；'.join(rollback_errors)}")
            undo_id = None
            if tx is None:
                try:
                    undo_id = owned.commit(detail)
                except Exception as commit_exc:
                    detail += f"；撤销记录写入失败：{commit_exc}"
            raise ApplyError(detail, undo_id=undo_id) from exc
        changed = sum(1 for a in actions if a.kind not in ("ok", "refused"))
        # 共享事务由外层操作统一记录审计，避免外层失败后残留成功记录。
        if tx is None:
            try:
                audit("apply", platform=platform or "*",
                      executed=changed,
                      refused=sum(1 for a in actions if a.kind == "refused"), undo=owned.id)
            except OSError as exc:
                warning = Action(platform or "*", only or "", "warning",
                                 f"apply 已完成且可撤销，但审计日志写入失败：{exc}")
                actions.append(warning)
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
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = target.with_name(target.name + ".bak-" + stamp)
        suffix = 1
        while backup.exists():
            backup = target.with_name(target.name + f".bak-{stamp}-{suffix}")
            suffix += 1
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
