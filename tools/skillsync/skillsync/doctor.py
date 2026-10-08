"""只读对账：扫描 canonical 仓与四平台目录，分类所有差异，不做任何修改。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .core import (
    PLATFORMS,
    SKILL_MD,
    SKILLS_DIR,
    SkillEntry,
    extract_description,
    frontmatter_issue,
    hash_skill,
    read_text_cached,
    scan_platform,
)
from .store import load_catalog, load_state

STATUS_LABELS = {
    "managed-ok": "已纳管（软链）",
    "synced-copy": "内容一致的副本（建议转软链）",
    "fork": "已分叉（内容不一致）",
    "unregistered": "未登记（可采纳）",
    "duplicate": "与仓内技能内容重复",
    "missing-target": "catalog 声明但平台缺失",
    "managed-foreign": "软链指向共享仓之外",
    "broken-link": "软链已失效",
    "missing-canonical": "catalog 有条目但 skills/ 缺目录",
    "bad-frontmatter": "SKILL.md 元数据异常",
    "ignored": "已忽略",
}

PROBLEM_STATUSES = {"fork", "broken-link", "missing-target", "managed-foreign",
                    "missing-canonical", "bad-frontmatter"}
ADOPTABLE_STATUSES = {"unregistered", "duplicate", "synced-copy"}


@dataclass
class Finding:
    platform: str
    name: str
    status: str
    detail: str = ""
    path: str = ""
    archived: bool = False  # catalog 归档标记：该技能的异常降级为不催办


def _canonical_index() -> tuple[dict[str, str], dict[str, list[str]]]:
    by_name: dict[str, str] = {}
    by_hash: dict[str, list[str]] = {}
    if SKILLS_DIR.exists():
        for child in sorted(SKILLS_DIR.iterdir()):
            if ".bak" in child.name:
                continue
            if child.is_dir() and (child / SKILL_MD).is_file():
                digest = hash_skill(child)
                by_name[child.name] = digest
                by_hash.setdefault(digest, []).append(child.name)
    return by_name, by_hash


def scan_all() -> dict[str, Any]:
    catalog = load_catalog()
    state = load_state()
    catalog_skills: dict[str, dict] = catalog.get("skills", {})
    ignored = set(state.get("ignored", []))

    canonical_by_name, canonical_by_hash = _canonical_index()
    findings: list[Finding] = []
    loose_files: dict[str, list[str]] = {}
    builtin_counts: dict[str, int] = {}
    platform_descriptions: dict[str, str] = {}  # 键 "platform/name" → description

    for key, platform in PLATFORMS.items():
        entries, loose = scan_platform(platform)
        loose_files[key] = loose

        # 内置技能（如 codex/.system）单独计数，不参与对账
        builtin_n = 0
        for dirname in platform.builtin_dirnames:
            builtin_root = platform.skills_dir / dirname
            if builtin_root.is_dir():
                builtin_n += sum(
                    1 for p in builtin_root.iterdir() if p.is_dir() and (p / SKILL_MD).is_file()
                )
        if builtin_n:
            builtin_counts[key] = builtin_n

        seen_names: set[str] = set()
        for e in entries:
            seen_names.add(e.name)
            # 顺带提取 description，避免 webapp 再扫一遍
            md = e.path / SKILL_MD
            if md.is_file():
                try:
                    desc = extract_description(read_text_cached(md))
                    if desc:
                        platform_descriptions[f"{key}/{e.name}"] = desc
                except OSError:
                    pass
            finding = _classify(e, catalog_skills, canonical_by_name, canonical_by_hash, ignored)
            findings.append(finding)
            # 元数据体检：坏 frontmatter 影响简介展示与检索，作为独立问题上报。
            # managed 软链穿透读到 canonical 本体，其元数据问题已在下方 canonical 侧
            # 统一报告，此处跳过避免同一问题重复 N 次（N = 下发平台数）。
            if finding.status != "managed-ok":
                issue = frontmatter_issue(e.path)
                if issue:
                    findings.append(Finding(e.platform, e.name, "bad-frontmatter", issue, str(e.path)))

        # catalog 声明分发到该平台、但目录里完全没有
        for slug, meta in catalog_skills.items():
            if key in (meta.get("platforms") or []) and slug not in seen_names:
                findings.append(
                    Finding(
                        platform=key,
                        name=slug,
                        status="missing-target",
                        detail="catalog.platforms 包含该平台，但平台目录中不存在",
                    )
                )

    # catalog 有但 canonical 目录缺失
    for slug in catalog_skills:
        if slug not in canonical_by_name:
            findings.append(
                Finding(platform="-", name=slug, status="missing-canonical",
                         detail="catalog.yaml 有条目，但 skills/ 下找不到对应目录")
            )

    # canonical 自身元数据体检
    for slug in canonical_by_name:
        issue = frontmatter_issue(SKILLS_DIR / slug)
        if issue:
            findings.append(
                Finding(platform="-", name=slug, status="bad-frontmatter", detail=issue)
            )

    # 归档语义：catalog 标记 archived 的技能，其全部异常/建议降级为「不催办」。
    # findings 仍全量返回（带 archived 标志），summary 只按未归档口径统计，
    # 这样卡片「需处理问题」「可采纳」自动排除已归档技能。
    for f in findings:
        if (catalog_skills.get(f.name) or {}).get("archived"):
            f.archived = True
    active = [f for f in findings if not f.archived]
    summary = _summarize(active, len(canonical_by_name), builtin_counts)
    return {
        "repo_root": str(SKILLS_DIR.parent),
        "canonical_count": len(canonical_by_name),
        "builtin_counts": builtin_counts,
        "loose_files": loose_files,
        "findings": [asdict(f) for f in findings],
        "archived_n": len(findings) - len(active),
        "summary": summary,
        "platform_descriptions": platform_descriptions,
    }


def _classify(
    e: SkillEntry,
    catalog_skills: dict[str, dict],
    canonical_by_name: dict[str, str],
    canonical_by_hash: dict[str, list[str]],
    ignored: set[str],
) -> Finding:
    ignore_key = f"{e.platform}/{e.name}"
    if ignore_key in ignored:
        return Finding(e.platform, e.name, "ignored", "在 state.ignored 名单中", str(e.path))

    if e.is_broken_link:
        target = os_readlink(e.path)
        return Finding(e.platform, e.name, "broken-link",
                       f"软链目标不存在：{target}", str(e.path))
    if e.is_symlink:
        if e.managed:
            digest = hash_skill(e.path)
            target_name = e.path.resolve().name
            if canonical_by_name.get(target_name) == digest:
                return Finding(e.platform, e.name, "managed-ok",
                               f"→ {e.path.resolve()}", str(e.path))
            return Finding(e.platform, e.name, "broken-link",
                           "软链目标存在但内容无法读取/不一致", str(e.path))
        return Finding(e.platform, e.name, "managed-foreign",
                       f"软链指向共享仓之外：{os_readlink(e.path)}", str(e.path))

    digest = hash_skill(e.path)
    if e.name in catalog_skills:
        canonical_digest = canonical_by_name.get(e.name)
        if canonical_digest is None:
            return Finding(e.platform, e.name, "missing-canonical",
                           "catalog 与平台都有，但 skills/ 缺 canonical 目录", str(e.path))
        if digest == canonical_digest:
            return Finding(e.platform, e.name, "synced-copy",
                           "普通目录副本，哈希与 canonical 一致", str(e.path))
        return Finding(e.platform, e.name, "fork",
                       "普通目录副本，哈希与 canonical 不一致，需要人工裁决", str(e.path))

    # 未登记
    twins = canonical_by_hash.get(digest, [])
    if twins:
        return Finding(e.platform, e.name, "duplicate",
                       f"内容与仓内技能完全相同：{', '.join(twins)}", str(e.path))
    return Finding(e.platform, e.name, "unregistered", "平台目录中的新技能，尚未纳入共享仓",
                   str(e.path))


def os_readlink(path: Path) -> str:
    try:
        return str(path.readlink())
    except OSError:
        return "(无法读取链接目标)"


def _summarize(findings: list[Finding], canonical_n: int,
               builtin_counts: dict[str, int]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    counts["canonical_total"] = canonical_n
    counts["builtin_total"] = sum(builtin_counts.values())
    return counts


def format_text(result: dict[str, Any]) -> str:  # noqa: C901 - 报告排版
    lines: list[str] = []
    lines.append(f"共享仓：{result['repo_root']}")
    lines.append(f"canonical 技能：{result['canonical_count']} 个；"
                 f"平台内置技能（不参与对账）：{result['summary'].get('builtin_total', 0)} 个")
    lines.append("")

    order = [
        "fork", "broken-link", "missing-target", "managed-foreign", "missing-canonical",
        "bad-frontmatter", "unregistered", "duplicate", "synced-copy", "managed-ok", "ignored",
    ]
    findings = [Finding(**f) for f in result["findings"]]
    for status in order:
        group = [f for f in findings if f.status == status]
        if not group:
            continue
        lines.append(f"## {STATUS_LABELS[status]}（{len(group)}）")
        for f in group:
            mark = "[已归档] " if f.archived else ""
            lines.append(f"  {mark}[{f.platform}] {f.name}" + (f"  — {f.detail}" if f.detail else ""))
        lines.append("")

    loose = result.get("loose_files", {})
    loose_msgs = [f"{p}: {', '.join(names)}" for p, names in loose.items() if names]
    if loose_msgs:
        lines.append("## 平台目录中的松散文件（非技能，不处理）")
        lines.extend(f"  {m}" for m in loose_msgs)
        lines.append("")

    s = result["summary"]
    problem_n = sum(s.get(k, 0) for k in PROBLEM_STATUSES)
    adoptable_n = sum(s.get(k, 0) for k in ADOPTABLE_STATUSES)
    lines.append(f"汇总：{problem_n} 个需处理问题，{adoptable_n} 个可采纳/可转软链，"
                 f"{s.get('managed-ok', 0)} 个已纳管。")
    archived_n = result.get("archived_n", 0)
    if archived_n:
        lines.append(f"另有 {archived_n} 条已归档技能的异常（不催办）。")
    return "\n".join(lines)
