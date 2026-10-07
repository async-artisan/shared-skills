"""新机器一键上手：扫描 → 下发已纳管技能软链 → 报告待采纳与问题。

不取代 doctor/apply/migrate，而是把它们串成新机器最短路径：
  1. scan_all 看本机现状
  2. apply 把 catalog 里已纳管的技能软链下发到本机（可 --dry-run）
  3. 重新扫描，汇总待采纳/问题，给出下一步建议

只做"安全写"：apply 内部已有软链原子替换与 undo Tx，bootstrap
不额外执行 adopt（采纳需用户确认来源，由控制台或 migrate 完成）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import apply as apply_mod
from . import doctor as doctor_mod
from .core import PLATFORMS, PLATFORMS_SOURCE


@dataclass
class BootstrapResult:
    before: dict[str, Any]
    apply_actions: list[Any]
    after: dict[str, Any]
    applied_n: int
    refused_n: int
    skip_apply: bool
    adoptable: list[dict[str, Any]] = field(default_factory=list)
    problem_n: int = 0


def run(dry_run: bool = False, skip_apply: bool = False) -> BootstrapResult:
    before = doctor_mod.scan_all()
    actions: list[Any] = []
    if not skip_apply:
        actions = apply_mod.run(write=not dry_run)
    after = doctor_mod.scan_all()

    applied = sum(1 for a in actions if a.kind not in ("ok", "refused"))
    refused = sum(1 for a in actions if a.kind == "refused")
    adoptable = [
        f for f in after["findings"]
        if f["status"] in doctor_mod.ADOPTABLE_STATUSES
    ]
    problem_n = sum(
        after["summary"].get(k, 0) for k in doctor_mod.PROBLEM_STATUSES
    )
    return BootstrapResult(
        before=before,
        apply_actions=actions,
        after=after,
        applied_n=applied,
        refused_n=refused,
        skip_apply=skip_apply,
        adoptable=adoptable,
        problem_n=problem_n,
    )


def format_text(r: BootstrapResult) -> str:
    lines: list[str] = []
    src = {"default": "内置默认", "repo": "仓库配置",
           "user": "个人配置", "env": "环境变量"}.get(PLATFORMS_SOURCE, PLATFORMS_SOURCE)
    pf = "、".join(f"{p.label}({p.key})" for p in PLATFORMS.values())
    lines.append(f"平台清单（来源：{src}）：{pf}")
    lines.append(f"canonical 技能：{r.after['canonical_count']} 个")
    lines.append("")

    if r.apply_actions:
        lines.append(f"软链下发：{r.applied_n} 项已创建/更新"
                     + ("（dry-run，未实际落盘）" if r.refused_n == 0 and not r.apply_actions else "")
                     + (f"，{r.refused_n} 项被安全规则拒绝" if r.refused_n else ""))
        for a in r.apply_actions:
            if a.kind == "ok":
                continue
            lines.append(f"  [{a.platform}] {a.slug} {a.kind} — {a.detail}")
        lines.append("")
    elif r.skip_apply:  # type: ignore[attr-defined]
        lines.append("已跳过软链下发（--no-apply）。")
        lines.append("")
    else:
        lines.append("软链下发：无需变更（所有已纳管技能均已在本机链接）。")
        lines.append("")

    if r.adoptable:
        lines.append(f"待采纳技能：{len(r.adoptable)} 个（平台侧散装，未进 catalog）")
        for f in r.adoptable[:20]:
            lines.append(f"  [{f['platform']}] {f['name']}"
                         + (f"  — {f['detail']}" if f.get("detail") else ""))
        if len(r.adoptable) > 20:
            lines.append(f"  ……还有 {len(r.adoptable) - 20} 个未列出")
        lines.append("")

    if r.problem_n:
        lines.append(f"问题信号：{r.problem_n} 条（断链/元数据异常等，详见 doctor）")
    else:
        lines.append("问题信号：0 条")
    lines.append("")

    next_steps: list[str] = []
    if r.problem_n:
        next_steps.append("先跑 `skillsync doctor` 定位问题并修复")
    if r.adoptable:
        next_steps.append("`skillsync serve` 打开控制台逐个采纳，或 `skillsync migrate <平台>` 批量采纳")
    if r.refused_n:
        next_steps.append("处理 apply 被拒绝的项后重新运行 `skillsync bootstrap`")
    if not next_steps:
        next_steps.append("环境就绪，可直接使用各平台的纳管技能")
    lines.append("下一步：")
    lines.extend(f"  - {s}" for s in next_steps)
    return "\n".join(lines)
