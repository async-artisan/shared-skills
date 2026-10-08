"""平台定义、路径常量、目录哈希与 SKILL.md frontmatter 解析。"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import yamllite
from .platforms import Platform, resolve_platforms

# 仓库根：tools/skillsync/skillsync/core.py 向上四级
_PKG_REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(os.environ.get("SKILLSYNC_HOME", str(_PKG_REPO_ROOT))).resolve()
SKILLS_DIR = REPO_ROOT / "skills"
REGISTRY_DIR = REPO_ROOT / "registry"
CATALOG_PATH = REGISTRY_DIR / "catalog.yaml"
CATALOG_EXAMPLE_PATH = REGISTRY_DIR / "catalog.example.yaml"
STATE_PATH = REGISTRY_DIR / "state.yaml"
AUDIT_PATH = REGISTRY_DIR / "audit.logl"

SKILL_MD = "SKILL.md"
IGNORE_FILES = {".DS_Store"}
IGNORE_DIRS = {"__pycache__", ".git"}

_DESC_KEY_RE = re.compile(r"^(description):\s*(.*)$")
# 顶层块标量：`key: >-` / `>` / `|-` / `|`（可选 chomping 指示 [-+]）
_BLOCK_SCALAR_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z_][\w-]*)\s*:\s*(?P<marker>[>|])(?P<chomp>[-+]?)\s*$"
)


def _normalize_block_scalars(block: str) -> str:
    """把 `key: >-` / `>` / `|-` / `|` 块标量展开为单行 quoted 字符串。

    yamllite 不支持块标量，但 SKILL.md 的 description 等长文本字段普遍
    使用该写法。在交给 yamllite 之前先预处理为单行单引号字符串，与
    extract_description 行为基本一致（folded/literal 都用空格连接，因
    yamllite 单/双引号均不支持反斜杠转义，无法在 quoted 字符串里保留换行）。
    """
    lines = block.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _BLOCK_SCALAR_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        indent = m.group("indent")
        key = m.group("key")
        chunk: list[str] = []
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if not ln.strip() or ln[:1] in (" ", "\t"):
                chunk.append(ln.strip())
                j += 1
            else:
                break
        text = " ".join(chunk).strip()
        # yamllite 不支持反斜杠转义；YAML 单引号用 `''` 表示一个 `'`。
        escaped = text.replace("'", "''")
        out.append(f"{indent}{key}: '{escaped}'")
        i = j
    return "\n".join(out)


def extract_description(md_text: str) -> str:
    """从 SKILL.md frontmatter 提取 description。

    自行扫描而非 yamllite：后者不支持 >、>- 等块标量，而技能文档
    的多行简介普遍使用该写法。支持折叠标量（拼成单行）、字面标量
    （保留换行）与单双引号行内标量。
    """
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return ""
    block = lines[1:end]
    for idx, line in enumerate(block):
        m = _DESC_KEY_RE.match(line)
        if not m:
            continue
        rest = m.group(2).strip()
        if rest.startswith((">", "|")):
            chunk: list[str] = []
            for l2 in block[idx + 1:]:
                if not l2.strip() or l2[:1] in (" ", "\t"):
                    chunk.append(l2.strip())
                else:
                    break
            return " ".join(chunk) if rest.startswith(">") else "\n".join(chunk)
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
            rest = rest[1:-1]
        return " ".join(rest.split())
    return ""


def frontmatter_issue(skill_dir: Path) -> str:
    """SKILL.md 元数据体检：返回问题描述，健康返回空串。

    宽松校验（不做完整 YAML 解析）：能容忍 yamllite 不支持的块标量，
    只盯真正影响展示与检索的三个点——结构缺失、description 空、
    name 与目录名不一致。
    """
    md = skill_dir / SKILL_MD
    try:
        text = read_text_cached(md)
    except OSError:
        return "SKILL.md 无法读取"
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "SKILL.md 缺少 frontmatter"
    end = -1
    for k in range(1, len(lines)):
        if lines[k].strip() == "---":
            end = k
            break
    if end < 0:
        return "frontmatter 未闭合（缺少结束 ---）"
    if not extract_description(text):
        return "description 缺失或为空"
    m = re.search(r"^name:\s*(.+)$", "\n".join(lines[1:end]), re.M)
    if m:
        nm = m.group(1).strip().strip("\"'")
        if nm and nm != skill_dir.name:
            return f"name 字段（{nm}）与目录名（{skill_dir.name}）不一致"
    return ""


# 生效平台注册表：registry/platforms.yaml / 用户级配置 / 内置默认。
# 进程启动时一次性快照；修改配置后重启 serve/CLI 生效。解析见 platforms.py。
_PLATFORMS_CFG = resolve_platforms(REGISTRY_DIR)
PLATFORMS = dict(_PLATFORMS_CFG.platforms)
# default / repo / user / env，供控制台展示当前平台清单来源
PLATFORMS_SOURCE = _PLATFORMS_CFG.source


@dataclass
class SkillEntry:
    """一次平台目录扫描得到的技能条目。"""

    platform: str
    name: str
    path: Path
    is_symlink: bool
    link_target: Path | None
    is_broken_link: bool
    is_builtin: bool
    exists: bool = True

    @property
    def managed(self) -> bool:
        return self.is_symlink and not self.is_broken_link and self._points_into_repo()

    def _points_into_repo(self) -> bool:
        try:
            self.link_target.resolve().relative_to(SKILLS_DIR.resolve())
            return True
        except (OSError, ValueError):
            return False


# ---- mtime/size 增量缓存（进程内存态，线程安全） ----
# 目录哈希只在运行内对账比较、从不落盘，因此组合方式可自由演进。
_CACHE_LOCK = threading.Lock()
_FILE_DIGEST_CACHE: dict[Path, tuple[int, int, str]] = {}  # 真实路径 -> (mtime_ns, size, sha256)
_TEXT_CACHE: dict[Path, tuple[int, int, str]] = {}         # 路径 -> (mtime_ns, size, 文本)
_CACHE_MAX = 8192


def read_text_cached(path: Path) -> str:
    """按 mtime/size 增量缓存的文本读取（errors=replace，SKILL.md 热路径专用）。"""
    st = path.stat()
    with _CACHE_LOCK:
        hit = _TEXT_CACHE.get(path)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    text = path.read_text(encoding="utf-8", errors="replace")
    with _CACHE_LOCK:
        if len(_TEXT_CACHE) >= _CACHE_MAX:
            _TEXT_CACHE.clear()
        _TEXT_CACHE[path] = (st.st_mtime_ns, st.st_size, text)
    return text


def iter_skill_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for name in sorted(filenames):
            if name in IGNORE_FILES:
                continue
            files.append(Path(dirpath) / name)
    return files


def hash_skill(root: Path) -> str:
    """对技能目录做确定性 sha256（相对路径 + 各文件摘要）。

    文件级摘要带 mtime/size 增量缓存，且以 resolve 后的真实路径为键：
    平台软链与 canonical 同源文件只读一次，未变文件跳过重读。
    文件列表每次仍全量遍历（仅 stat），新增/删除文件自然改变目录哈希。
    root 只 resolve 一次，逐文件复用 base/rel 拼出真实路径，避免
    大规模扫描时逐文件 realpath 的系统调用开销。
    """
    h = hashlib.sha256()
    base = root.resolve()
    for f in iter_skill_files(root):
        rel = f.relative_to(root)
        h.update(rel.as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(_file_digest(base / rel).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def _file_digest(real: Path) -> str:
    """单文件 sha256 摘要（十六进制）；mtime/size 未变时直接命中缓存。"""
    st = real.stat()
    with _CACHE_LOCK:
        hit = _FILE_DIGEST_CACHE.get(real)
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    digest = hashlib.sha256(real.read_bytes()).hexdigest()
    with _CACHE_LOCK:
        if len(_FILE_DIGEST_CACHE) >= _CACHE_MAX:
            _FILE_DIGEST_CACHE.clear()
        _FILE_DIGEST_CACHE[real] = (st.st_mtime_ns, st.st_size, digest)
    return digest


def parse_frontmatter(skill_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """返回 (frontmatter dict, 错误信息)。解析失败时 dict 为 None。"""
    path = skill_dir / SKILL_MD
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, f"无法读取 {path}"
    if not text.startswith("---"):
        return None, "SKILL.md 缺少 frontmatter 起始分隔符 ---"
    lines = text.splitlines()
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return None, "SKILL.md frontmatter 缺少结束分隔符 ---"
    block = "\n".join(lines[1:end])
    block = _normalize_block_scalars(block)
    try:
        data = yamllite.parse(block)
    except yamllite.YamlLiteError as exc:
        return None, f"frontmatter 解析失败：{exc}"
    if not isinstance(data, dict):
        return None, "frontmatter 必须是键值对结构"
    return data, None


def scan_platform(platform: Platform) -> tuple[list[SkillEntry], list[str]]:
    """扫描平台技能目录，返回 (技能条目, 其它松散文件提示)。"""
    entries: list[SkillEntry] = []
    loose: list[str] = []
    base = platform.skills_dir
    if not base.exists():
        return entries, loose
    for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if child.name in IGNORE_FILES or child.name.startswith("."):
            if child.is_file():
                loose.append(child.name)
            continue
        if ".bak-" in child.name or ".canonical.bak-" in child.name:
            # 裁决/下发产生的备份目录，不参与对账
            continue
        is_builtin = child.name in platform.builtin_dirnames
        is_link = child.is_symlink()
        target = None
        broken = False
        if is_link:
            try:
                target = os.readlink(child)
                if not Path(os.path.realpath(child)).exists():
                    broken = True
            except OSError:
                broken = True
        if child.is_dir() and not broken and (child / SKILL_MD).is_file():
            entries.append(
                SkillEntry(
                    platform=platform.key,
                    name=child.name,
                    path=child,
                    is_symlink=is_link,
                    link_target=Path(target) if target else None,
                    is_broken_link=broken,
                    is_builtin=is_builtin,
                )
            )
        elif is_link and broken:
            entries.append(
                SkillEntry(
                    platform=platform.key,
                    name=child.name,
                    path=child,
                    is_symlink=True,
                    link_target=Path(target) if target else None,
                    is_broken_link=True,
                    is_builtin=False,
                )
            )
        elif child.is_file():
            loose.append(child.name)
    return entries, loose
