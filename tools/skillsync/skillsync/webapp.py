"""skillsync 本机 Web 控制台。

设计约束：
- 仅 Python 标准库（http.server），仅绑定 127.0.0.1，无登录体系；
- 不引入第二状态源：所有数据实时来自 doctor/adopt/resolve/apply/store；
- 页面不提供在线编辑，只做只读看板 + 受控动作触发，写操作由底层模块保证
  “默认 dry-run / 先备份 / 留审计”。
"""
from __future__ import annotations

import contextlib
import copy
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import unicodedata
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import adopt as adopt_mod
from . import apply as apply_mod
from . import remote as remote_mod
from . import resolve as resolve_mod
from . import undo as undo_mod
from .core import (
    AUDIT_PATH,
    IGNORE_DIRS,
    PLATFORMS,
    PLATFORMS_SOURCE,
    SKILL_MD,
    SKILLS_DIR,
    _file_digest,
    extract_description,
    hash_skill,
    iter_skill_files,
    parse_frontmatter,
    read_text_cached,
    scan_platform,
)
from .doctor import PROBLEM_STATUSES, scan_all
from .store import audit, load_catalog, load_state, save_catalog, save_state
from .adopt import AdoptError
from .resolve import ResolveError
from .security import scan_skill

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code  # 机器可读标识，供前端区分失败类型（如能力缺失）


def _error_payload(exc: ApiError) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": str(exc)}
    if exc.code:
        payload["code"] = exc.code
    return payload


def _platforms_payload() -> dict[str, Any]:
    """当前进程生效的平台清单（启动快照，改配置需重启 serve）。"""
    return {
        "platforms": [
            {"key": p.key, "label": p.label}
            for p in PLATFORMS.values()
        ],
        "platforms_source": PLATFORMS_SOURCE,
    }


def _diff_skill(platform_key: str, name: str) -> dict[str, Any]:
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        raise ApiError(f"未知平台：{platform_key}")
    entries, _ = scan_platform(platform)
    entry = next((item for item in entries if item.name == name), None)
    if entry is None:
        raise ApiError(f"平台目录中找不到：{name}")

    canonical = SKILLS_DIR / name
    plat_lines = (entry.path / SKILL_MD).read_text(
        encoding="utf-8", errors="replace").splitlines()

    if not (canonical / SKILL_MD).is_file():
        return {
            "name": name,
            "platform": platform_key,
            "has_canonical": False,
            "diff": "（仓内尚无同名技能；采纳后此副本将成为 canonical）",
            "platform_skill_md": "\n".join(plat_lines),
        }

    canon_lines = (canonical / SKILL_MD).read_text(
        encoding="utf-8", errors="replace").splitlines()
    diff = "\n".join(difflib.unified_diff(
        canon_lines, plat_lines,
        fromfile=f"canonical/{name}/SKILL.md",
        tofile=f"{platform_key}/{name}/SKILL.md",
        lineterm="", n=3,
    )) or "（SKILL.md 无差异；差异可能在 references/scripts 等附属文件）"
    return {
        "name": name,
        "platform": platform_key,
        "has_canonical": True,
        "same_hash": hash_skill(canonical) == hash_skill(entry.path),
        "diff": diff,
        "canonical_skill_md": "\n".join(canon_lines),
        "platform_skill_md": "\n".join(plat_lines),
    }


def _compare_skill(platform_key: str, name: str) -> dict[str, Any]:
    """分叉对比：逐文件比对 canonical 与平台实体目录（拉取更新决策视图）。"""
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        raise ApiError(f"未知平台：{platform_key}")
    entries, _ = scan_platform(platform)
    entry = next((item for item in entries if item.name == name), None)
    if entry is None:
        raise ApiError(f"平台目录中找不到：{name}", 404)
    if entry.is_symlink:
        raise ApiError("平台侧是软链，与共享仓同源，不存在可拉取的更新")
    canonical = SKILLS_DIR / name
    if not (canonical / SKILL_MD).is_file():
        raise ApiError("canonical 缺少该技能目录，属「采纳」场景而非分叉")

    def side_map(root: Path) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for f in iter_skill_files(root):
            rel = f.relative_to(root).as_posix()
            st = f.stat()
            out[rel] = {"size": st.st_size, "mtime": int(st.st_mtime),
                        "digest": _file_digest(f.resolve())}
        return out

    repo_side = side_map(canonical)      # 共享仓侧
    plat_side = side_map(entry.path)     # 平台侧
    files: list[dict[str, Any]] = []
    counts = {"added": 0, "removed": 0, "changed": 0, "same": 0}
    for rel in sorted(set(repo_side) | set(plat_side)):
        if rel in repo_side and rel in plat_side:
            status = "same" if repo_side[rel]["digest"] == plat_side[rel]["digest"] else "changed"
        elif rel in plat_side:
            status = "added"
        else:
            status = "removed"
        counts[status] += 1
        files.append({
            "rel": rel, "status": status,
            "size_a": repo_side.get(rel, {}).get("size"),
            "mtime_a": repo_side.get(rel, {}).get("mtime"),
            "size_b": plat_side.get(rel, {}).get("size"),
            "mtime_b": plat_side.get(rel, {}).get("mtime"),
        })
    identical = counts == {"added": 0, "removed": 0, "changed": 0, "same": len(files)}
    diff = None
    if not identical and (canonical / SKILL_MD).is_file() and (entry.path / SKILL_MD).is_file():
        canon_lines = (canonical / SKILL_MD).read_text(
            encoding="utf-8", errors="replace").splitlines()
        plat_lines = (entry.path / SKILL_MD).read_text(
            encoding="utf-8", errors="replace").splitlines()
        diff = "\n".join(difflib.unified_diff(
            canon_lines, plat_lines,
            fromfile=f"canonical/{name}/SKILL.md",
            tofile=f"{platform_key}/{name}/SKILL.md",
            lineterm="", n=3,
        )) or "（SKILL.md 无文本差异；改动在附属文件）"
    return {"name": name, "platform": platform_key, "files": files,
            "counts": counts, "identical": identical, "diff": diff}


def _skill_file(slug: str) -> dict[str, str]:
    from .store import load_catalog
    if slug not in load_catalog().get("skills", {}):
        raise ApiError("技能未登记 catalog", 404)
    path = SKILLS_DIR / slug / SKILL_MD
    if not path.is_file():
        raise ApiError("canonical 缺少 SKILL.md", 404)
    return {"slug": slug, "content": path.read_text(encoding="utf-8", errors="replace")}


def _source_skill(platform_key: str, name: str) -> dict[str, str]:
    """读取平台目录中的 SKILL.md 原文（采纳前预览用，只读不写）。"""
    platform = PLATFORMS.get(platform_key)
    if platform is None:
        raise ApiError(f"未知平台：{platform_key}")
    entries, _ = scan_platform(platform)
    entry = next((item for item in entries if item.name == name), None)
    if entry is None:
        raise ApiError(f"平台目录中找不到：{name}", 404)
    path = entry.path / SKILL_MD
    if not path.is_file():
        raise ApiError("该技能目录缺少 SKILL.md", 404)
    return {
        "platform": platform_key,
        "name": name,
        "content": path.read_text(encoding="utf-8", errors="replace"),
    }


# ---- 英译中：Argos Translate（MIT 开源、完全离线） ----
#
# 仅做"看懂英文技能说明"的只读辅助翻译：进程内加载本地 en→zh 模型，
# 翻译过程中用占位符保护 frontmatter、围栏代码块、行内代码、URL 与
# markdown 标记，译文只用于网页预览，绝不回写任何文件。

_TRANS_LOCK = threading.Lock()
_TRANSLATION_CACHE: dict[str, Any] = {}
_RESULT_CACHE: dict[str, str] = {}
_RESULT_CACHE_MAX = 64
# 译文磁盘缓存：重启不丢；lazy 加载 + 原子写
_DISK_CACHE_PATH = AUDIT_PATH.parent / "translate-cache.json"
_DISK_CACHE: dict[str, str] | None = None
_DISK_CACHE_MAX = 512
_TOKEN_RE = re.compile(r"`[^`\n]+`|https?://[^\s)]+|\*\*|__")
# Argos 模型在超短行上偶尔吐出字幕样式标签（如 {\fn方正粗倩简体\fs12...}）
_ASS_TAG_RE = re.compile(r"\{\\[^{}]*\}")
_LEAD_MARKER_RE = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+|>\s?)")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.S)


def _is_mostly_chinese(text: str) -> bool:
    cjk = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    return cjk >= max(3, latin // 4)


def _translate_markdown(text: str) -> str:
    try:
        import argostranslate.settings as argos_settings
        import argostranslate.translate as argos_tr
    except ImportError as exc:
        raise ApiError(
            "翻译引擎未安装：python3 -m pip install argostranslate", 503,
            code="no-translate-engine") from exc
    # 强制纯 Python 分句器，避免 stanza 模型去 huggingface 联网下载
    argos_settings.chunk_type = argos_settings.ChunkType.MINISBD

    with _TRANS_LOCK:
        translation = _TRANSLATION_CACHE.get("en->zh")
        if translation is None:
            languages = argos_tr.get_installed_languages()
            en = next((l for l in languages if l.code == "en"), None)
            zh = next((l for l in languages if l.code.startswith("zh")), None)
            if en is None or zh is None:
                raise ApiError(
                    "缺少 en→zh 离线语言包："
                    "python3 -m argos install en zh（或用 argostranslate 下载）", 503,
                    code="no-translate-engine")
            translation = en.get_translation(zh)
            _TRANSLATION_CACHE["en->zh"] = translation

    store: list[str] = []

    def stash(match: re.Match[str]) -> str:
        store.append(match.group(0))
        return f"TK{len(store) - 1}TK"

    out_lines: list[str] = []
    in_fence = False
    body = text
    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        out_lines.append(fm_match.group(0).rstrip("\n"))
        body = text[fm_match.end():]

    for line in body.splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence or not line.strip():
            out_lines.append(line)
            continue
        # 行首 markdown 标记（标题/列表/引用）位置固定，翻译后原位补回
        lead = _LEAD_MARKER_RE.match(line)
        prefix = lead.group(0) if lead else ""
        rest = line[len(prefix):]
        protected = _TOKEN_RE.sub(stash, rest)
        translated = translation.translate(protected)
        translated = _ASS_TAG_RE.sub("", translated)
        # 还原占位符（顺序可能被模型调整，按编号回填）
        for idx, original in enumerate(store):
            translated = translated.replace(f"TK{idx}TK", original)
        if prefix and not translated.lstrip().startswith(prefix.strip()):
            translated = prefix + translated.lstrip()
        out_lines.append(translated)

    return "\n".join(out_lines)


def _disk_cache() -> dict[str, str]:
    global _DISK_CACHE
    if _DISK_CACHE is None:
        try:
            loaded = json.loads(_DISK_CACHE_PATH.read_text(encoding="utf-8"))
            _DISK_CACHE = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            _DISK_CACHE = {}
    return _DISK_CACHE


def _disk_cache_save() -> None:
    if _DISK_CACHE is None:
        return
    while len(_DISK_CACHE) > _DISK_CACHE_MAX:
        oldest = next(iter(_DISK_CACHE))
        _DISK_CACHE.pop(oldest, None)
    tmp = _DISK_CACHE_PATH.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(_DISK_CACHE, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_DISK_CACHE_PATH)
    except OSError:
        pass


def _do_translate(body: dict[str, Any]) -> dict[str, Any]:
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ApiError("text 必填")
    if len(text) > 60_000:
        raise ApiError("单次翻译上限 60KB")
    if _is_mostly_chinese(text):
        return {"ok": True, "skipped": True, "engine": "none",
                "reason": "已是中文内容", "translated": text}
    cache_key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    with _TRANS_LOCK:
        cached = _RESULT_CACHE.get(cache_key)
    if cached is None:
        cached = _disk_cache().get(cache_key)
        if cached is not None:
            with _TRANS_LOCK:
                _RESULT_CACHE[cache_key] = cached
    if cached is not None:
        return {"ok": True, "skipped": False,
                "engine": "argos-translate · 离线 en→zh（缓存）", "translated": cached}
    translated = _translate_markdown(text)
    with _TRANS_LOCK:
        if len(_RESULT_CACHE) >= _RESULT_CACHE_MAX:
            _RESULT_CACHE.clear()
        _RESULT_CACHE[cache_key] = translated
        _disk_cache()[cache_key] = translated
        _disk_cache_save()
    return {"ok": True, "skipped": False,
            "engine": "argos-translate · 离线 en→zh", "translated": translated}


def _scan_signature() -> str:
    """技能目录变化签名：canonical 与各平台目录下每个条目的 mtime_ns/size 哈希。

    递归收集目录与文件（不跟随目录软链），因此改 SKILL.md、references/ 等
    任意层级文件的内容都会改变签名；平台侧的软链技能由 canonical 侧覆盖。
    """
    parts: list[str] = []

    def collect(root: Path) -> None:
        try:
            st = root.stat()
        except OSError:
            return
        parts.append(f"{root}:{st.st_mtime_ns}:{st.st_size}")
        stack = [root]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    entries = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            for entry in entries:
                if entry.name in IGNORE_DIRS or entry.name.startswith("."):
                    continue
                try:
                    est = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                parts.append(f"{entry.path}:{est.st_mtime_ns}:{est.st_size}")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))

    collect(SKILLS_DIR)
    for key in sorted(PLATFORMS):
        collect(PLATFORMS[key].skills_dir)
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


# ---- 动作处理（均复用 CLI 同一批函数） ----

def _do_adopt(body: dict[str, Any]) -> dict[str, Any]:
    platform = body.get("platform")
    name = body.get("name")
    if not platform or not name:
        raise ApiError("platform/name 必填")
    targets = body.get("targets") or None
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = adopt_mod.adopt(
            platform_key=platform, name=name,
            target_platforms=targets,
            link=bool(body.get("link")),
            conflict="skip",
        )
    return {"ok": True, "result": asdict(result), "scan_notes": buf.getvalue().strip()}


def _do_resolve(body: dict[str, Any]) -> dict[str, Any]:
    winner = body.get("winner")
    if winner not in ("canonical", "platform"):
        raise ApiError("winner 必须是 canonical 或 platform")
    result = resolve_mod.resolve(body.get("platform", ""), body.get("name", ""),
                                 winner=winner, dry_run=False)
    return {"ok": True, "result": asdict(result)}


def _do_apply(body: dict[str, Any]) -> dict[str, Any]:
    try:
        actions = apply_mod.run(
            write=bool(body.get("write")),
            platform=body.get("platform") or None,
            only=body.get("only") or None,
            link=bool(body.get("link")),
            force=bool(body.get("force")),
        )
    except apply_mod.ApplyError as exc:
        retry = f"；撤销 ID：{exc.undo_id}" if exc.undo_id else ""
        raise ApiError(f"{exc}{retry}", 409, "apply_failed") from exc
    return {
        "ok": True,
        "dry_run": not bool(body.get("write")),
        "actions": [asdict(a) for a in actions],
        "refused": [asdict(a) for a in actions if a.kind == "refused"],
    }


def _do_ignore(body: dict[str, Any]) -> dict[str, Any]:
    platform, name = body.get("platform"), body.get("name")
    if not platform or not name:
        raise ApiError("platform/name 必填")
    key = f"{platform}/{name}"
    state = load_state()
    ignored = set(state.get("ignored", []))
    add = not bool(body.get("remove"))
    tx = undo_mod.Tx("ignore", name)
    tx.state_ignored(key, add)  # 撤销 = 反向恢复
    if body.get("remove"):
        ignored.discard(key)
    else:
        ignored.add(key)
    state["ignored"] = sorted(ignored)
    save_state(state)
    audit("ignore", slug=name, platform=platform, remove=bool(body.get("remove")),
          via="web", undo=tx.id)
    tx.commit(f"{'忽略' if add else '取消忽略'} {key}")
    return {"ok": True, "ignored": sorted(ignored)}


def _do_archive(body: dict[str, Any]) -> dict[str, Any]:
    """归档/取消归档：在 catalog 技能条目上写 archived 标记（退役标记，不再催办其任何异常）。"""
    from .store import load_catalog, save_catalog

    slug = str(body.get("slug") or "").strip()
    if not slug:
        raise ApiError("slug 必填")
    archived = body.get("archived")
    if not isinstance(archived, bool):
        raise ApiError("archived 必须为布尔值")
    catalog = load_catalog()
    entry = (catalog.get("skills", {}) or {}).get(slug)
    if entry is None:
        raise ApiError(f"catalog 未登记该技能：{slug}", 404)
    prev = copy.deepcopy(entry)
    tx = undo_mod.Tx("archive", slug)
    tx.catalog_set(slug, copy.deepcopy(prev))  # 撤销 = 整条恢复为操作前条目
    if archived:
        entry["archived"] = True
        entry["archived_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    else:
        entry.pop("archived", None)
        entry.pop("archived_at", None)
    save_catalog(catalog)
    audit("archive", slug=slug, archived=archived, via="web", undo=tx.id)
    tx.commit(f"{'归档' if archived else '取消归档'} {slug}")
    return {"ok": True, "slug": slug, "archived": archived}


_IMPORT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _do_import_preview(body: dict[str, Any]) -> dict[str, Any]:
    """远程 URL → 技能预览（含文件内容，但不落盘）。"""
    url = str(body.get("url") or "").strip()
    if not url:
        raise ApiError("url 必填")
    try:
        preview = remote_mod.fetch(url)
    except remote_mod.RemoteError as exc:
        raise ApiError(str(exc))
    return {"ok": True, "preview": {k: v for k, v in preview.items() if k != "blobs"}}


def _do_import_confirm(body: dict[str, Any]) -> dict[str, Any]:
    """确认导入：同一 URL 重新抓取（保证最新），校验名称后写入 canonical 并登记 catalog。"""
    url = str(body.get("url") or "").strip()
    name = str(body.get("name") or "").strip()
    binding = str(body.get("binding") or "").strip()
    if not url:
        raise ApiError("url 必填")
    if not _IMPORT_NAME_RE.match(name):
        raise ApiError("导入名称需为小写字母/数字/连字符（≤64 位，字母开头）")
    if not binding:
        raise ApiError("缺少预览绑定，请重新解析远程地址")
    try:
        preview = remote_mod.fetch(url)
    except remote_mod.RemoteError as exc:
        raise ApiError(str(exc))
    if remote_mod.preview_binding(preview) != binding:
        raise ApiError("远程内容已变化，请重新解析预览后再确认导入")
    if str(preview.get("name") or "").strip() != name:
        raise ApiError(f"预览名称（{preview.get('name') or '缺失'}）与导入名称（{name}）不一致")
    dest = SKILLS_DIR / name
    if dest.exists() or dest.is_symlink():
        raise ApiError(f"skills/{name} 已存在，请改用其他名称")
    catalog_before = load_catalog()
    if name in catalog_before.get("skills", {}):
        raise ApiError(f"catalog 已登记技能：{name}，请先处理现有条目")
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{name}.import-", dir=str(SKILLS_DIR)))
    tx = undo_mod.Tx("import", name)
    tx.rm_tree(dest)  # 撤销 = 删除导入目录
    created_dest = False
    catalog_saved = False
    try:
        written = 0
        file_meta = {str(item.get("path")): item for item in preview.get("files", [])}
        if len(file_meta) != len(preview.get("files", [])):
            raise ApiError("预览文件列表包含重复路径")
        if set(file_meta) != set(preview.get("blobs", {})):
            raise ApiError("预览文件清单与下载内容不一致")
        normalized_paths: dict[str, str] = {}
        path_parts: dict[str, tuple[str, ...]] = {}
        for rel, data in preview["blobs"].items():
            parts = PurePosixPath(rel).parts
            if (not parts or "\\" in rel
                    or any(p in ("", ".", "..") for p in rel.split("/"))
                    or PurePosixPath(rel).is_absolute()):
                raise ApiError(f"远端返回非法路径：{rel}")
            if rel != SKILL_MD and rel.endswith(f"/{SKILL_MD}"):
                raise ApiError(f"远端技能目录不能包含嵌套的 {SKILL_MD}：{rel}")
            if rel == "SKILL.md" and len(parts) != 1:
                raise ApiError(f"远端 SKILL.md 必须位于技能根目录：{rel}")
            path_key = unicodedata.normalize("NFC", rel).casefold()
            previous = normalized_paths.get(path_key)
            if previous is not None and previous != rel:
                raise ApiError(f"远端文件路径在本机文件系统上可能重合：{previous} / {rel}")
            normalized_paths[path_key] = rel
            meta = file_meta.get(rel)
            if meta is None:
                raise ApiError(f"预览缺少文件摘要：{rel}")
            if int(meta.get("size") or 0) != len(data):
                raise ApiError(f"远端文件大小与预览不一致：{rel}")
            if str(meta.get("sha256") or "") != hashlib.sha256(data).hexdigest():
                raise ApiError(f"远端文件摘要与预览不一致：{rel}")
            path_parts[rel] = parts
        normalized_path_keys = set(normalized_paths)
        for path_key, rel in normalized_paths.items():
            components = path_key.split("/")
            if any("/".join(components[:index]) in normalized_path_keys
                   for index in range(1, len(components))):
                raise ApiError(f"远端文件与目录路径在本机文件系统上冲突：{rel}")
        if "SKILL.md" not in file_meta:
            raise ApiError("导入目录必须包含根 SKILL.md")
        for rel, data in preview["blobs"].items():
            target = temp_dir / Path(*path_parts[rel])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written += 1
        if written == 0 or not (temp_dir / SKILL_MD).is_file():
            raise ApiError("导入目录必须包含根 SKILL.md")
        expected_paths = set(file_meta)
        actual_paths = {
            f.relative_to(temp_dir).as_posix() for f in iter_skill_files(temp_dir)
        }
        if actual_paths != expected_paths:
            raise ApiError("导入文件落盘后与预览清单不一致")
        for rel in expected_paths:
            target = temp_dir / Path(*PurePosixPath(rel).parts)
            if target.read_bytes() != preview["blobs"][rel]:
                raise ApiError(f"导入文件落盘校验失败：{rel}")
        # 目录名即技能名：与 frontmatter.name 不一致会被 verify 判 FAIL，
        # 且技能库行简介也取不到，故落盘后即校验，不达标不登记
        fm, err = parse_frontmatter(temp_dir)
        if fm is None:
            raise ApiError(f"导入的 SKILL.md frontmatter 不合规：{err}")
        fm_name = str(fm.get("name") or "").strip()
        if fm_name != name:
            raise ApiError(f"frontmatter name（{fm_name or '缺失'}）与导入名称（{name}）不一致："
                           "本工具不改写技能内容，请把导入名称改成一致后重试")
        if not str(fm.get("description") or "").strip():
            raise ApiError("导入的 SKILL.md frontmatter 缺少 description")
        warnings = scan_skill(temp_dir)
        failures = [w for w in warnings if w.get("level") == "FAIL"]
        if failures:
            details = "；".join(f"{w.get('file', '')} {w.get('rule', '')}" for w in failures)
            raise ApiError(f"远程技能安全扫描失败，已阻止导入：{details}")
        if dest.exists() or dest.is_symlink():
            raise ApiError(f"skills/{name} 在导入期间已出现，请重新预览并重试")
        os.replace(temp_dir, dest)
        created_dest = True
        # 登记 catalog：技能库、归档与 verify 都以 catalog 为账本，
        # 不登记则导入结果不进任何页签。不声明 platforms：导入只进仓，下发另行决定。
        catalog = copy.deepcopy(catalog_before)
        tx.catalog_set(name, None)
        imported_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        catalog["skills"][name] = {
            "source_platform": "remote",
            "platforms": [],
            "tools_required": [],
            "depends_on": [],
            "description_zh": "",
            "adopted_at": imported_at,
            "imported_at": imported_at,
            "imported_from": preview["source"],
        }
        catalog_saved = True
        save_catalog(catalog)
        tx.commit(f"导入 {name}（{written} 个文件）")
    except Exception as exc:
        cleanup_errors: list[str] = []
        # 失败路径必须清理临时目录和目标目录，避免失败导入变成半成品技能。
        for path in (temp_dir, dest if created_dest else None):
            if path is None:
                continue
            try:
                if path.is_symlink() or path.is_file():
                    path.unlink(missing_ok=True)
                elif path.is_dir():
                    shutil.rmtree(path)
            except OSError as cleanup_exc:
                cleanup_errors.append(f"删除 {path.name} 失败：{cleanup_exc}")
        if catalog_saved:
            try:
                save_catalog(catalog_before)
            except Exception as cleanup_exc:
                cleanup_errors.append(f"恢复 catalog 失败：{cleanup_exc}")
        undo_path = SKILLS_DIR.parent / "registry" / "undo" / f"{tx.id}.json"
        try:
            undo_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            cleanup_errors.append(f"清理 undo 记录失败：{cleanup_exc}")
        if cleanup_errors:
            raise ApiError(f"导入失败且清理未完成：{exc}；{'；'.join(cleanup_errors)}", 500) from exc
        raise
    if temp_dir.exists() or temp_dir.is_symlink():
        shutil.rmtree(temp_dir, ignore_errors=True)
    audit_warning = ""
    try:
        audit("import", slug=name, platform="canonical",
              source=preview["source"], files=written, via="web", undo=tx.id,
              security_warnings=sum(1 for w in warnings if w["level"] == "WARN"))
    except OSError as exc:
        audit_warning = f"技能已导入且可撤销，但审计日志写入失败：{exc}"
    return {"ok": True, "name": name, "files": written,
            "description": preview["description"],
            "security_warnings": warnings,
            "audit_warning": audit_warning}


def _list_undo() -> dict[str, Any]:
    return {"ok": True, "entries": undo_mod.list_undo(200)}


def _do_undo(body: dict[str, Any]) -> dict[str, Any]:
    uid = str(body.get("id") or "").strip()
    if not uid:
        raise ApiError("id 必填")
    try:
        return undo_mod.execute(uid)
    except undo_mod.UndoError as exc:
        raise ApiError(str(exc))


def _canonical_descriptions() -> dict[str, str]:
    """提取 canonical 各技能 frontmatter 的 description，作为列表简介。"""
    out: dict[str, str] = {}
    if not SKILLS_DIR.is_dir():
        return out
    for child in sorted(SKILLS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or ".bak-" in child.name:
            continue
        md = child / SKILL_MD
        if not md.is_file():
            continue
        try:
            out[child.name] = extract_description(read_text_cached(md))
        except OSError:
            out[child.name] = ""
    return out


def _platform_descriptions() -> dict[str, str]:
    """提取各平台目录技能的 description，键为 platform/name（采纳前预览用）。"""
    out: dict[str, str] = {}
    for key, platform in PLATFORMS.items():
        entries, _loose = scan_platform(platform)
        for entry in entries:
            md = entry.path / SKILL_MD
            if not md.is_file():
                continue
            try:
                desc = extract_description(read_text_cached(md))
            except OSError:
                continue
            if desc:
                out[f"{key}/{entry.name}"] = desc
    return out


def _read_audit(limit: int = 300) -> list[dict[str, Any]]:
    """读取 audit.logl（JSONL），最新在前；坏行跳过。"""
    if not AUDIT_PATH.is_file():
        return []
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rec.setdefault("time", "")
            rec.setdefault("action", "")
            rec.setdefault("slug", "")
            rec.setdefault("platform", "")
            out.append(rec)
    out.reverse()
    return out[: max(1, limit)]


# 进程内写锁：ThreadingHTTPServer 下并发请求可能交叉执行 load→改→save，
# 导致 catalog/state 丢更新。所有 POST 写动作串行化，避免竞态。
_write_lock = threading.Lock()

# POST body 大小上限（1MB），防止恶意大请求耗尽内存
_MAX_POST_BODY = 1 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "skillsync/0.1"

    # 静默默认访问日志；动作审计在业务层落 audit.logl
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # 安全校验：阻断 CSRF 和 DNS-rebinding
    _LOOPBACK_HOSTS = frozenset({
        "127.0.0.1", "localhost", "::1",
        "[::1]",  # IPv6 带方括号的形式
    })

    def _check_local_origin(self) -> None:
        """POST 写操作必须来自本机回环，阻断跨站请求和 DNS-rebinding。"""
        # 1. Origin 头校验（现代浏览器对所有 POST 都会发此头）
        origin = self.headers.get("Origin")
        if origin:
            try:
                o = urlparse(origin)
                if o.hostname not in self._LOOPBACK_HOSTS:
                    raise ApiError(f"拒绝跨站请求：Origin 不是本机回环", 403)
            except ApiError:
                raise
            except Exception:
                raise ApiError("Origin 头格式不合规", 403)
        # 2. Host 头校验（防 DNS-rebinding：攻击者把恶意域名 DNS 解析到 127.0.0.1）
        host = self.headers.get("Host", "")
        # 去端口号
        host_name = host.split(":")[0].lower() if host else ""
        if host_name and host_name not in self._LOOPBACK_HOSTS:
            raise ApiError(f"拒绝非本机 Host：{host}", 403)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _send_html(self, path: Path) -> None:
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in ("/", "/index.html"):
                self._send_html(INDEX_HTML)
                return
            if parsed.path == "/api/platforms":
                self._send_json({"ok": True, **_platforms_payload()})
                return
            if parsed.path == "/api/scan":
                state = load_state()
                result = scan_all()
                result.update(_platforms_payload())
                result["state"] = {"ignored": state.get("ignored", []),
                                   "last_apply": state.get("last_apply", {})}
                result["problem_total"] = sum(
                    result["summary"].get(k, 0) for k in PROBLEM_STATUSES)
                result["descriptions"] = _canonical_descriptions()
                # platform_descriptions 已在 scan_all 中提取，无需再扫一遍
                catalog_skills = load_catalog().get("skills", {})
                result["archived"] = sorted(
                    slug for slug, m in catalog_skills.items() if m.get("archived"))
                # catalog 账本清单：技能库据此列出「已登记但未下发」的技能
                result["catalog_slugs"] = sorted(catalog_skills)
                result["catalog_target_count"] = sum(
                    len(meta.get("platforms") or [])
                    for meta in catalog_skills.values()
                    if isinstance(meta, dict))
                self._send_json(result)
                return
            if parsed.path == "/api/diff":
                q = parse_qs(parsed.query)
                self._send_json(_diff_skill(q.get("platform", [""])[0],
                                            q.get("name", [""])[0]))
                return
            if parsed.path == "/api/compare":
                q = parse_qs(parsed.query)
                self._send_json(_compare_skill(q.get("platform", [""])[0],
                                               q.get("name", [""])[0]))
                return
            if parsed.path == "/api/skill":
                q = parse_qs(parsed.query)
                self._send_json(_skill_file(q.get("slug", [""])[0]))
                return
            if parsed.path == "/api/source":
                q = parse_qs(parsed.query)
                self._send_json(_source_skill(q.get("platform", [""])[0],
                                              q.get("name", [""])[0]))
                return
            if parsed.path == "/api/history":
                q = parse_qs(parsed.query)
                try:
                    limit = min(2000, max(1, int(q.get("limit", ["300"])[0])))
                except ValueError:
                    limit = 300
                self._send_json({"ok": True, "entries": _read_audit(limit)})
                return
            if parsed.path == "/api/poll":
                self._send_json({"ok": True, "sig": _scan_signature()})
                return
            if parsed.path == "/api/undo":
                self._send_json(_list_undo())
                return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except ApiError as exc:
            self._send_json(_error_payload(exc), exc.status)
        except Exception as exc:  # 单用户本机工具：完整错误回传便于排障
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            # 安全校验：阻断 CSRF 和 DNS-rebinding
            self._check_local_origin()
            length = int(self.headers.get("Content-Length") or 0)
            if length > _MAX_POST_BODY:
                self._send_json({"ok": False, "error": f"请求体过大（{length} > {_MAX_POST_BODY}）"}, 413)
                return
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            routes = {
                "/api/adopt": _do_adopt,
                "/api/resolve": _do_resolve,
                "/api/apply": _do_apply,
                "/api/ignore": _do_ignore,
                "/api/archive": _do_archive,
                "/api/translate": _do_translate,
                "/api/import": _do_import_preview,
                "/api/import/confirm": _do_import_confirm,
                "/api/undo": _do_undo,
            }
            handler = routes.get(parsed.path)
            if handler is None:
                self._send_json({"ok": False, "error": "not found"}, 404)
                return
            # 写操作串行化：避免并发请求下 catalog/state load→改→save 竞态丢更新
            with _write_lock:
                self._send_json(handler(body))
        except (ApiError, AdoptError, ResolveError) as exc:
            if isinstance(exc, ApiError):
                self._send_json(_error_payload(exc), exc.status)
            else:
                self._send_json({"ok": False, "error": str(exc)}, 400)
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "请求体不是合法 JSON"}, 400)
        except Exception as exc:
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)


def serve(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> tuple[ThreadingHTTPServer, str]:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("本机控制台只允许绑定回环地址")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    actual_host, actual_port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{actual_host}:{actual_port}/"
    if open_browser:
        threading.Timer(0.4, lambda: _open_browser(url)).start()
    return httpd, url


def _open_browser(url: str) -> None:
    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        pass
