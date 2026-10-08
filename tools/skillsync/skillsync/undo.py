"""写操作撤销：每个写动作在落盘前记录逆操作指令，撤销时逆序回放。

设计：
- adopt/apply/resolve/ignore/import 在改动任何路径前，先把"如何回到
  改动前"写成指令追加进 Tx（事务式收集），成功后一次性写入
  registry/undo/<id>.json；失败则丢弃，不产生残留记录；
- 撤销 = 逆序执行指令 + 标记 executed（防重复）+ audit 留痕；
- 所有路径指令执行前校验必须位于 canonical 或平台技能目录之下，
  防止记录文件被篡改后任意删改。

指令集：
- rm_tree        删除目录树（含软链场景退化成 unlink）
- rm_link        仅当目标为软链时删除
- restore_link   恢复软链原指向（prev 为空则等价 rm_link）
- unbak          删除现软链并把 .bak-<ts> 实体目录改回原名
- restore_bak_dir 目标清空后把备份目录改回原名（canonical 覆盖撤销）
- catalog_set    恢复/移除 catalog 中的技能条目
- state_ignored  恢复 ignored 状态
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import PLATFORMS, SKILLS_DIR
from .store import audit, load_catalog, load_state, save_catalog, save_state


class UndoError(Exception):
    pass


def _undo_dir() -> Path:
    d = SKILLS_DIR.parent / "registry" / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Tx:
    """单次写操作的逆操作收集器；commit 前不产生任何文件。"""

    def __init__(self, action: str, slug: str = ""):
        self.id = f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{action}"
        self.action = action
        self.slug = slug
        self.ops: list[dict[str, Any]] = []

    # ---- 指令记录（必须在真实落盘动作之前调用） ----
    def rm_tree(self, path: Path) -> None:
        self.ops.append({"op": "rm_tree", "path": str(path)})

    def rm_link(self, path: Path) -> None:
        self.ops.append({"op": "rm_link", "path": str(path)})

    def restore_link(self, path: Path, prev: str | None) -> None:
        self.ops.append({"op": "restore_link", "path": str(path), "prev": prev})

    def unbak(self, bak: Path, target: Path) -> None:
        self.ops.append({"op": "unbak", "bak": str(bak), "target": str(target)})

    def restore_bak_dir(self, bak: Path, target: Path) -> None:
        self.ops.append({"op": "restore_bak_dir", "bak": str(bak), "target": str(target)})

    def catalog_set(self, slug: str, entry: dict[str, Any] | None) -> None:
        self.ops.append({"op": "catalog_set", "slug": slug, "entry": entry})

    def state_ignored(self, key: str, add: bool) -> None:
        self.ops.append({"op": "state_ignored", "key": key, "add": add})

    def commit(self, detail: str) -> str:
        rec = {"id": self.id, "time": _now(), "action": self.action, "slug": self.slug,
               "detail": detail, "executed": False, "executed_at": "", "ops": self.ops}
        (_undo_dir() / f"{self.id}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.id

    def discard(self) -> None:
        """失败路径：不写文件，GC 无需清理。"""


# ---- 执行 ----
def _allowed_root(path: Path) -> bool:
    roots = [SKILLS_DIR.resolve()] + [PLATFORMS[k].skills_dir.resolve() for k in PLATFORMS]
    rp = path.resolve()
    return any(rp == r or r in rp.parents for r in roots)


def _allowed_link_path(path: Path) -> bool:
    """验证软链目录项的位置，不跟随软链本身的目标。"""
    if path.name in ("", ".", ".."):
        return False
    parent = path.parent.resolve()
    roots = [SKILLS_DIR.resolve()] + [PLATFORMS[k].skills_dir.resolve() for k in PLATFORMS]
    return any(parent == root or root in parent.parents for root in roots)


def _rm_tree(path: Path) -> None:
    # 幂等：已不存在视为成功（撤销重试场景下目标可能已被清理）
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _apply_op(op: dict[str, Any]) -> str:
    kind = op["op"]
    if kind in ("rm_tree", "rm_link", "restore_link", "unbak", "restore_bak_dir"):
        p = Path(op["path"]) if "path" in op else Path(op["target"])
        bak = Path(op["bak"]) if kind in ("unbak", "restore_bak_dir") else None
        if kind in ("rm_link", "restore_link"):
            if not _allowed_link_path(p):
                raise UndoError(f"撤销指令路径越界：{p}")
        elif not _allowed_root(p):
            raise UndoError(f"撤销指令路径越界：{p}")
        if bak is not None and not _allowed_root(bak):
            raise UndoError(f"撤销指令路径越界：{bak}")
    if kind == "rm_tree":
        _rm_tree(Path(op["path"]))
        return f"已删除 {op['path']}"
    if kind == "rm_link":
        t = Path(op["path"])
        if t.is_symlink():
            t.unlink()
            return f"已移除软链 {op['path']}"
        if not t.exists():
            return f"目标已不存在，跳过 {op['path']}"
        raise UndoError(f"目标已变为非软链对象，无法安全移除：{op['path']}")
    if kind == "restore_link":
        t = Path(op["path"])
        if t.is_symlink():
            t.unlink()
        elif t.exists() and not t.is_symlink():
            raise UndoError(f"目标已变为非软链对象，无法安全恢复：{op['path']}")
        prev = op.get("prev")
        if prev:
            os.symlink(prev, t)
            return f"软链恢复指向 {prev}"
        return f"已移除软链（原不存在）{op['path']}"
    if kind == "unbak":
        t, bak = Path(op["target"]), Path(op["bak"])
        if not bak.exists():
            return f"备份 {bak.name} 不存在，跳过"
        if t.is_symlink():
            t.unlink()
        bak.rename(t)
        return f"{bak.name} 已还原为 {t.name}"
    if kind == "restore_bak_dir":
        t, bak = Path(op["target"]), Path(op["bak"])
        if not bak.exists():
            return f"备份 {bak.name} 不存在，跳过"
        _rm_tree(t)
        bak.rename(t)
        return f"{bak.name} 已还原为 {t.name}"
    if kind == "catalog_set":
        catalog = load_catalog()
        if op["entry"] is None:
            catalog.get("skills", {}).pop(op["slug"], None)
        else:
            catalog.setdefault("skills", {})[op["slug"]] = op["entry"]
        save_catalog(catalog)
        return f"catalog 条目 {op['slug']} 已{'恢复' if op['entry'] else '移除'}"
    if kind == "state_ignored":
        state = load_state()
        ignored = set(state.get("ignored", []))
        # 撤销 = 反向操作：写入时 add=True（加入忽略），撤销时 discard（移除）
        reverse_add = not op["add"]
        (ignored.add if reverse_add else ignored.discard)(op["key"])
        state["ignored"] = sorted(ignored)
        save_state(state)
        return f"ignored 状态 {op['key']} 已{'恢复' if reverse_add else '移除'}"
    raise UndoError(f"未知撤销指令：{kind}")


def execute(uid: str) -> dict[str, Any]:
    path = _undo_dir() / f"{Path(uid).name}.json"
    if not path.is_file():
        raise UndoError(f"找不到撤销记录：{uid}")
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("executed"):
        raise UndoError("该操作已撤销过，不能重复撤销")
    # 逐 op 执行：失败的 op 记录错误后继续，最后标记 partial 并落盘，
    # 避免中途崩溃留下半成品且不可重试。
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for op in reversed(rec["ops"]):
        try:
            msg = _apply_op(op)
            results.append({"op": op["op"], "result": msg})
        except UndoError as exc:
            errors.append(f"{op['op']}: {exc}")
            results.append({"op": op["op"], "error": str(exc)})
        except OSError as exc:
            errors.append(f"{op['op']}: {exc}")
            results.append({"op": op["op"], "error": str(exc)})
    if errors:
        rec["executed"] = False
        rec["partial"] = True
        rec["partial_at"] = _now()
        rec["partial_errors"] = errors
        path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        raise UndoError(f"撤销部分失败（{len(errors)} 步），已完成 {len(results) - len(errors)} 步；"
                        f"可重试 undo {uid}，已执行的幂等步骤会自动跳过")
    rec["executed"] = True
    rec["executed_at"] = _now()
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    audit("undo", slug=rec.get("slug", ""), platform="*",
          ref=rec["id"], original=rec["action"])
    return {"ok": True, "id": rec["id"], "results": results}


def list_undo(limit: int = 100) -> list[dict[str, Any]]:
    out = []
    for f in sorted(_undo_dir().glob("*.json"), reverse=True)[: max(1, limit)]:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({k: rec.get(k) for k in
                    ("id", "time", "action", "slug", "detail", "executed")})
    return out
