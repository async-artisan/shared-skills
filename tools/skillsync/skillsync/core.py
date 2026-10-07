"""平台定义、路径常量、目录哈希与 SKILL.md frontmatter 解析。"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import yamllite

# 仓库根：tools/skillsync/skillsync/core.py 向上四级
_PKG_REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = Path(os.environ.get("SKILLSYNC_HOME", str(_PKG_REPO_ROOT))).resolve()
SKILLS_DIR = REPO_ROOT / "skills"
REGISTRY_DIR = REPO_ROOT / "registry"
CATALOG_PATH = REGISTRY_DIR / "catalog.yaml"
STATE_PATH = REGISTRY_DIR / "state.yaml"
AUDIT_PATH = REGISTRY_DIR / "audit.logl"

SKILL_MD = "SKILL.md"
IGNORE_FILES = {".DS_Store"}
IGNORE_DIRS = {"__pycache__", ".git"}

_DESC_KEY_RE = re.compile(r"^(description):\s*(.*)$")


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
        text = md.read_text(encoding="utf-8", errors="replace")
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


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    skills_dir: Path
    # 相对于 skills_dir 需要整体跳过的内置目录名
    builtin_dirnames: frozenset[str] = frozenset()


def _platforms() -> dict[str, Platform]:
    home = Path.home()
    return {
        "codex": Platform(
            key="codex",
            label="Codex",
            skills_dir=home / ".codex" / "skills",
            builtin_dirnames=frozenset({".system"}),
        ),
        "claude": Platform(
            key="claude",
            label="Claude",
            skills_dir=home / ".claude" / "skills",
        ),
        "workbuddy": Platform(
            key="workbuddy",
            label="WorkBuddy",
            skills_dir=home / ".workbuddy" / "skills",
        ),
        "trae": Platform(
            key="trae",
            label="TRAE",
            skills_dir=home / ".trae-cn" / "skills",
        ),
    }


PLATFORMS = _platforms()


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
    """对技能目录做确定性 sha256（相对路径 + 文件内容）。"""
    h = hashlib.sha256()
    base = root.resolve()
    for f in iter_skill_files(root):
        rel = f.resolve().relative_to(base).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


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
