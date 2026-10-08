"""多机 git 共享流。

随公开仓分发（跨机/跨人同步）：工具代码与仓骨架（tools/、bin/、manifests/ 等）。
私有数据（不入库，由 .gitignore 兜底）：skills/ 个人技能库、registry/catalog.yaml
个人台账、registry/state.yaml、audit.logl、undo/、translate-cache.json。
需要跨机同步私有技能库时，请自建私有远端（私有 fork 或第二个 remote）并在该仓
调整 .gitignore——公开远端的 push 不会携带这些数据。

历史上误跟踪的 audit.logl 由 push 时的 ensure_local_untracked 移出索引。
老版本曾把 registry/catalog.yaml 入库；pull 到「私有化」迁移提交时，
_preserve_catalog_before_pull 会先备份本机台账，merge 后再还原，避免私有数据丢失。

三个命令均只封装 git 原生命令，坚持安全策略：
- sync-status：fetch 后给出 ahead/behind、未提交变更分类、.bak 残留与 catalog 一致性；
- push：预检（无未合并冲突、无 .bak 残留、catalog 与 skills/ 一致、不落后远端）
  后 git add -A + 中文自动提交 + push（首次自动 -u）；
- pull：受跟踪文件有本地改动时拒绝；默认 --ff-only，分叉默认拒绝（--rebase 才变基）。

不提供 force/reset 等破坏性操作；远端 URL 输出时脱敏 userinfo。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import CATALOG_PATH, PLATFORMS, REGISTRY_DIR, REPO_ROOT, SKILLS_DIR
from .doctor import scan_all
from .store import audit

# 机器本地路径（曾被误跟踪的统一用 git rm --cached 移出索引，本地文件保留）
LOCAL_PATHS = ["registry/audit.logl", "registry/undo", "registry/translate-cache.json"]

# 私有台账相对仓根路径；老版本曾入库，pull 私有化提交时需要专门保护
CATALOG_REL = "registry/catalog.yaml"


class GitError(Exception):
    """git 命令执行失败。"""

    def __init__(self, message: str, returncode: int = 1):
        super().__init__(message)
        self.returncode = returncode


class PreflightError(Exception):
    """预检拒绝（不执行任何写操作）。"""

    def __init__(self, reasons: list[str]):
        super().__init__("；".join(reasons))
        self.reasons = reasons


def _git(*args: str, check: bool = True, timeout: int | None = None,
         capture: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), text=True,
        capture_output=capture, timeout=timeout)
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"git 退出码 {proc.returncode}"
        raise GitError(f"git {' '.join(args[:2])}… 失败：{tail}", proc.returncode)
    return proc


def _lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [l for l in proc.stdout.splitlines() if l]


def is_repo() -> bool:
    try:
        return _git("rev-parse", "--is-inside-work-tree", check=False).returncode == 0
    except OSError:
        return False


def current_branch() -> str:
    # symbolic-ref 在尚无提交的空仓（unborn 分支）上也能取到分支名
    proc = _git("symbolic-ref", "--short", "HEAD", check=False)
    if proc.returncode == 0:
        return proc.stdout.strip()
    return "(detached)"


def remote_url(remote: str) -> str:
    out = _git("remote", "get-url", remote, check=False)
    return out.stdout.strip() if out.returncode == 0 else ""


def _redact(url: str) -> str:
    return re.sub(r"(://)[^/@]+@", r"\1***@", url)


def has_upstream() -> bool:
    return _git("rev-parse", "--abbrev-ref", "@{u}", check=False).returncode == 0


def _porcelain() -> list[tuple[str, list[str]]]:
    """git status --porcelain=v1 -> [(XY, [paths...])]，简单处理 rename/普通行。"""
    out = _lines(_git("status", "--porcelain=v1", "--untracked-files=all"))
    rows: list[tuple[str, list[str]]] = []
    for line in out:
        xy, rest = line[:2], line[3:]
        if " -> " in rest:
            old, new = rest.split(" -> ", 1)
            paths = [old.strip(), new.strip()]
        else:
            paths = [rest.strip()]
        if paths and all(paths):
            rows.append((xy, paths))
    return rows


def _tracked_dirty() -> list[str]:
    """受跟踪文件的未提交改动（含暂存/未暂存；不含未跟踪新文件）。"""
    out = _lines(_git("status", "--porcelain=v1", "--untracked-files=no"))
    paths = []
    for line in out:
        rest = line[3:]
        paths.append(rest.split(" -> ")[-1].strip())
    return [p for p in paths if p]


def _unmerged() -> list[str]:
    return _lines(_git("diff", "--name-only", "--diff-filter=U"))


def _bak_leftovers() -> list[str]:
    """apply/resolve 遗留的 .bak-时间戳 目录（回滚副本未处理，禁止跨机传播）。"""
    found: list[str] = []
    roots = [SKILLS_DIR] + [p.skills_dir for p in PLATFORMS.values()]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob(".bak-*"):
            found.append(str(p.relative_to(REPO_ROOT)))
    return sorted(found)


def _catalog_consistency() -> tuple[list[str], list[str]]:
    """返回 (阻断项, 警告项)：missing-canonical 阻断；canonical 元数据异常仅警告。"""
    result = scan_all()
    blocking = sorted({f["name"] for f in result["findings"]
                       if f["status"] == "missing-canonical" and not f.get("archived")})
    warnings = sorted({f["name"] for f in result["findings"]
                       if f["status"] == "bad-frontmatter" and f["platform"] == "-"
                       and not f.get("archived")})
    return blocking, warnings


def ensure_local_untracked() -> list[str]:
    """把机器本地路径移出 git 索引（--cached，本地文件保留）。返回被移出的路径。"""
    tracked = _lines(_git("ls-files", "--", *LOCAL_PATHS))
    if tracked:
        _git("rm", "--cached", "-r", "--quiet", "--ignore-unmatch", "--", *LOCAL_PATHS)
    return tracked


def incoming_catalog_deletion() -> bool:
    """fetch 后判断 upstream 是否将 registry/catalog.yaml 移出跟踪。

    老版本 catalog.yaml 随仓分发，私有化迁移提交会在 tree 中删除它。
    用 HEAD...@{u} 三点 diff 检测「远端侧」的删除，避免把用户本地的
    未提交删除误判为传入变更。
    """
    proc = _git("diff", "--name-only", "--diff-filter=D", "HEAD...@{u}", check=False)
    return proc.returncode == 0 and CATALOG_REL in _lines(proc)


def preserve_catalog_before_pull() -> Path | None:
    """pull 私有化迁移提交前，把本机私有台账移出将被 merge 删除的路径。

    1. 备份为 registry/catalog.yaml.pre-pull-<时间戳>（已被 .gitignore 排除）；
    2. git rm --cached 同步移除索引——staged deletion 与传入删除方向一致，
       fast-forward 可直接通过，merge 后再由 restore_catalog_after_pull 还原。
    返回备份路径；无需保护时返回 None。
    """
    if not incoming_catalog_deletion() or not CATALOG_PATH.exists():
        return None
    backup = REGISTRY_DIR / f"catalog.yaml.pre-pull-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(CATALOG_PATH), str(backup))
    if _git("ls-files", "--error-unmatch", CATALOG_REL, check=False).returncode == 0:
        _git("rm", "--cached", "--quiet", "--", CATALOG_REL)
    return backup


def restore_catalog_after_pull(backup: Path | None) -> bool:
    """把 pull 前备份的私有台账还原为被 ignore 的本机文件；返回是否还原。"""
    if backup is None or not backup.exists() or CATALOG_PATH.exists():
        return False
    shutil.copy2(str(backup), str(CATALOG_PATH))
    return True


def fetch(remote: str, timeout: int = 30) -> None:
    _git("fetch", "--prune", remote, timeout=timeout)


def ahead_behind(remote: str) -> tuple[int, int]:
    """相对 upstream 的 (ahead, behind)；无 upstream 返回 (0, 0)。"""
    if not has_upstream():
        return (0, 0)
    out = _git("rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split()
    return int(out[0]), int(out[1])


def sync_status(remote: str = "origin", do_fetch: bool = True) -> dict[str, Any]:
    if not is_repo():
        raise PreflightError([f"目录不是 git 仓库：{REPO_ROOT}（先 git init 并配置 {remote} 远端）"])
    url = remote_url(remote)
    branch = current_branch()
    info: dict[str, Any] = {
        "repo": str(REPO_ROOT), "branch": branch, "remote": remote,
        "remote_url": _redact(url) if url else "", "has_remote": bool(url),
        "has_upstream": has_upstream(), "ahead": 0, "behind": 0,
        "fetch_ok": False, "dirty": [], "untracked": [], "staged": [],
        "bak": [], "blocking": [], "warnings": [], "can_push": False, "can_pull": False,
        "reasons": [],
    }
    if not url:
        info["reasons"].append(f"未配置远端 {remote}（git remote add {remote} <url> 后启用多机流）")
        # 本地侧状态仍照常收集
        rows = _porcelain()
        info["dirty"] = sorted({p for xy, ps in rows if xy != "??" for p in ps})
        info["untracked"] = sorted({p for xy, ps in rows if xy == "??" for p in ps})
        info["staged"] = sorted({p for xy, ps in rows if xy[0] in "MADRC" for p in ps})
        info["bak"] = _bak_leftovers()
        return info
    if do_fetch:
        try:
            fetch(remote)
            info["fetch_ok"] = True
        except (GitError, subprocess.TimeoutExpired) as exc:
            info["reasons"].append(f"fetch 失败（可 --offline 看本地状态）：{exc}")
    if has_upstream():
        info["ahead"], info["behind"] = ahead_behind(remote)
    rows = _porcelain()
    info["dirty"] = sorted({p for xy, ps in rows if xy != "??" for p in ps})
    info["untracked"] = sorted({p for xy, ps in rows if xy == "??" for p in ps})
    info["staged"] = sorted({p for xy, ps in rows if xy[0] in "MADRC" for p in ps})
    info["bak"] = _bak_leftovers()
    blocking, warnings = _catalog_consistency()
    info["blocking"] = blocking
    info["warnings"] = warnings
    reasons = list(info["reasons"])
    unmerged = _unmerged()
    if unmerged:
        reasons.append("存在未合并冲突，请先解决")
    if info["bak"]:
        reasons.append(f"存在 {len(info['bak'])} 个 .bak 回滚副本残留")
    if blocking:
        reasons.append(f"catalog 声明但 skills/ 缺失：{', '.join(blocking[:5])}")
    if info["behind"]:
        reasons.append(f"落后远端 {info['behind']} 个提交，push 前先 pull")
    info["reasons"] = reasons
    info["can_push"] = not any(r for r in reasons if "冲突" in r or ".bak" in r
                               or "缺失" in r or "落后远端" in r) and info["fetch_ok"]
    info["can_pull"] = not info["dirty"] and not _unmerged()
    if not info["can_pull"] and (info["dirty"] or _unmerged()):
        info["reasons"].append("受跟踪文件有本地改动/冲突，pull 前先提交或 stash")
    return info


def _stage_summary() -> dict[str, Any]:
    """暂存区变更分类（自动提交信息用）。"""
    out = _lines(_git("diff", "--cached", "--name-status"))
    added, updated, deleted, others = set(), set(), set(), set()
    catalog_changed = False
    for line in out:
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code, path = parts[0][0], parts[-1]
        if path == "registry/catalog.yaml":
            catalog_changed = True
        seg = path.split("/")
        if seg[0] == "skills" and len(seg) >= 2:
            bucket = {"A": added, "D": deleted}.get(code, updated)
            bucket.add(seg[1])
        else:
            others.add(path)
    return {"added": sorted(added), "updated": sorted(updated),
            "deleted": sorted(deleted), "others": sorted(others),
            "catalog_changed": catalog_changed, "n_files": len(out)}


def _commit_message(s: dict[str, Any]) -> str:
    bits = []
    if s["added"]:
        bits.append(f"新增技能 {len(s['added'])} 个")
    if s["updated"]:
        bits.append(f"更新技能 {len(s['updated'])} 个")
    if s["deleted"]:
        bits.append(f"移除技能 {len(s['deleted'])} 个")
    if s["catalog_changed"]:
        bits.append("catalog 变更")
    if s["others"]:
        bits.append(f"仓骨架变更 {len(s['others'])} 项")
    title = "chore(sync): 多机同步（" + "，".join(bits) + "）" if bits else "chore(sync): 空同步提交"
    body = []
    if s["added"]:
        body.append("新增技能：" + "、".join(s["added"]))
    if s["updated"]:
        body.append("更新技能：" + "、".join(s["updated"]))
    if s["deleted"]:
        body.append("移除技能：" + "、".join(s["deleted"]))
    if s["catalog_changed"]:
        body.append("registry/catalog.yaml 同步")
    if s["others"]:
        body.append("其他：" + "、".join(s["others"][:10]))
    return title + ("\n\n" + "\n".join(body) if body else "")


def push(remote: str = "origin", message: str | None = None) -> dict[str, Any]:
    if not is_repo():
        raise PreflightError([f"目录不是 git 仓库：{REPO_ROOT}"])
    if not remote_url(remote):
        raise PreflightError([f"未配置远端 {remote}（git remote add {remote} <url>）"])
    unmerged = _unmerged()
    bak = _bak_leftovers()
    blocking, warnings = _catalog_consistency()
    reasons: list[str] = []
    if unmerged:
        reasons.append("存在未合并冲突，请先解决：" + "、".join(unmerged[:5]))
    if bak:
        reasons.append("存在 .bak 回滚副本残留（apply/resolve 未收尾）：" + "、".join(bak[:5]))
    if blocking:
        reasons.append("catalog 声明但 skills/ 缺失，推送会让其他机器缺技能："
                       + "、".join(blocking[:5]))
    try:
        fetch(remote)
    except (GitError, subprocess.TimeoutExpired) as exc:
        reasons.append(f"fetch 失败，拒绝在状态不明时推送：{exc}")
    if has_upstream():
        ahead, behind = ahead_behind(remote)
        if behind:
            reasons.append(f"落后远端 {behind} 个提交，请先 skillsync pull")
    if reasons:
        raise PreflightError(reasons)

    moved = ensure_local_untracked()
    _git("add", "-A")
    staged = _lines(_git("diff", "--cached", "--name-only"))
    committed = False
    commit = ""
    if staged:
        summary = _stage_summary()
        msg = message or _commit_message(summary)
        proc = _git("commit", "-m", msg)
        committed = True
        commit = _git("rev-parse", "--short", "HEAD").stdout.strip()
        _ = proc
    branch = current_branch()
    push_args = ["push"]
    if not has_upstream():
        push_args += ["-u", remote, branch]
    else:
        push_args += [remote]
    _git(*push_args, timeout=120)
    ahead, behind = ahead_behind(remote) if has_upstream() else (0, 0)
    audit("sync-push", platform=remote, remote=_redact(remote_url(remote)),
          commit=commit, committed=committed, moved_local=moved,
          warnings=warnings, ahead=ahead, behind=behind)
    return {"ok": True, "committed": committed, "commit": commit, "pushed": True,
            "branch": branch, "moved_local": moved, "warnings": warnings,
            "summary": _stage_summary() if committed else None}


def pull(remote: str = "origin", rebase: bool = False) -> dict[str, Any]:
    if not is_repo():
        raise PreflightError([f"目录不是 git 仓库：{REPO_ROOT}"])
    if not remote_url(remote):
        raise PreflightError([f"未配置远端 {remote}（git remote add {remote} <url>）"])
    fetch(remote)
    if not has_upstream():
        raise PreflightError([f"当前分支没有上游（未 push 过）；先 skillsync push -u 建链"])
    ahead, behind = ahead_behind(remote)
    # catalog 私有化迁移保护：必须在 dirty 预检与 merge 之前完成（备份 + 移出索引）
    catalog_backup = preserve_catalog_before_pull() if behind else None
    dirty = [p for p in _tracked_dirty()
             if not (catalog_backup is not None and p == CATALOG_REL)]
    unmerged = _unmerged()
    reasons: list[str] = []
    if unmerged:
        reasons.append("存在未合并冲突：" + "、".join(unmerged[:5]))
    if dirty:
        reasons.append("受跟踪文件有未提交改动，请先提交或 stash：" + "、".join(dirty[:8]))
    if reasons:
        restore_catalog_after_pull(catalog_backup)
        raise PreflightError(reasons)
    fast_forwarded = False
    rebased = False
    if behind == 0:
        strategy = "up-to-date"
    else:
        if ahead == 0:
            proc = _git("merge", "--ff-only", "@{u}", check=False)
            if proc.returncode != 0:
                restore_catalog_after_pull(catalog_backup)
                raise GitError("fast-forward 合并失败："
                               + (proc.stderr or proc.stdout).strip()[-400:])
            fast_forwarded = True
            strategy = "fast-forward"
        else:
            # 存在 catalog 保护动作（staged deletion）时 rebase 无法执行，
            # 备份已安全落盘，明确拒绝并给出人工恢复路径
            if catalog_backup is not None:
                restore_catalog_after_pull(catalog_backup)
                raise PreflightError([
                    "远端已将 catalog.yaml 私有化且本地存在分叉提交，无法自动迁移。"
                    f"本机台账备份在 {catalog_backup}，请先人工 rebase/合并后再恢复"])
            if not rebase:
                raise PreflightError([
                    f"本地与远端已分叉（本地领先 {ahead}、落后 {behind}）："
                    "默认拒绝合并，确认后用 skillsync pull --rebase 变基"])
            proc = _git("rebase", "@{u}", check=False)
            if proc.returncode != 0:
                _git("rebase", "--abort", check=False)
                restore_catalog_after_pull(catalog_backup)
                raise GitError("rebase 失败已自动 abort，请人工处理冲突")
            rebased = True
            strategy = "rebase"
    catalog_restored = restore_catalog_after_pull(catalog_backup)
    # 拉取后跑一次 doctor：提示新机需要 apply 下发的规模
    result = scan_all()
    s = result["summary"]
    missing_target = s.get("missing-target", 0)
    post = {
        "managed_ok": s.get("managed-ok", 0),
        "missing_target": missing_target,
        "problem_total": sum(s.get(k, 0) for k in ("fork", "broken-link", "missing-target",
                                                   "managed-foreign", "missing-canonical",
                                                   "bad-frontmatter")),
        "adoptable": sum(s.get(k, 0) for k in ("unregistered", "duplicate", "synced-copy")),
    }
    audit("sync-pull", platform=remote, remote=_redact(remote_url(remote)),
          strategy=strategy, ahead_before=ahead, behind_before=behind,
          fast_forwarded=fast_forwarded, rebased=rebased,
          catalog_preserved=bool(catalog_backup), catalog_restored=catalog_restored,
          catalog_backup=str(catalog_backup) if catalog_backup else "")
    return {"ok": True, "strategy": strategy, "ahead_before": ahead,
            "behind_before": behind, "fast_forwarded": fast_forwarded,
            "rebased": rebased, "catalog_restored": catalog_restored,
            "catalog_backup": str(catalog_backup) if catalog_backup else "",
            "post_scan": post,
            "apply_hint": (f"有 {missing_target} 个 catalog 技能尚未下发本机平台，"
                           "可运行 skillsync apply --write --link") if missing_target else ""}
