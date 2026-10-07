"""skillsync 命令行入口。

子命令：
  doctor    只读对账，报告分叉/未登记/失效软链
  adopt     把某个平台技能复制进共享仓并登记
  migrate   批量采纳某平台的全部未登记技能
  apply     按 catalog 把 canonical 软链下发到各平台（默认 dry-run）
  verify    canonical 仓自检
  serve     本机 Web 控制台（下一阶段提供）
  sync-status / push / pull  多机 git 共享流（只同步 skills/ 与 catalog.yaml 等事实源）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from . import __version__, adopt as adopt_mod
from . import apply as apply_mod
from . import bootstrap as bootstrap_mod
from . import doctor as doctor_mod
from . import gitsync as gitsync_mod
from . import resolve as resolve_mod
from . import verify as verify_mod
from .adopt import AdoptError
from .core import PLATFORMS
from .doctor import ADOPTABLE_STATUSES, scan_all
from .gitsync import GitError, PreflightError
from .store import audit, load_state, save_state


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_bootstrap(args: argparse.Namespace) -> int:
    result = bootstrap_mod.run(
        dry_run=args.dry_run, skip_apply=args.no_apply)
    if args.json:
        _print_json({
            "applied": result.applied_n,
            "refused": result.refused_n,
            "adoptable": len(result.adoptable),
            "problem": result.problem_n,
            "actions": [asdict(a) for a in result.apply_actions],
        })
        return 1 if (result.refused_n or result.problem_n) else 0
    print(bootstrap_mod.format_text(result))
    return 1 if (result.refused_n or result.problem_n) else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    result = scan_all()
    if args.json:
        _print_json(result)
    else:
        print(doctor_mod.format_text(result))
    if args.strict:
        problem = sum(result["summary"].get(k, 0) for k in doctor_mod.PROBLEM_STATUSES)
        return 1 if problem else 0
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    try:
        result = adopt_mod.adopt(
            platform_key=args.platform,
            name=args.name,
            target_platforms=args.targets.split(",") if args.targets else None,
            link=args.link,
            conflict=args.conflict,
            source_path=args.from_path,
        )
    except AdoptError as exc:
        print(f"[拒绝] {exc}", file=sys.stderr)
        return 2
    print(f"[{result.status}] {result.slug}（来源 {result.source_platform}）：{result.detail}")
    if result.security_warnings:
        print(f"  安全提示 {result.security_warnings} 条（仅提示，请人工确认）")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    result = scan_all()
    candidates = [
        f for f in result["findings"]
        if f["platform"] == args.platform and f["status"] in ADOPTABLE_STATUSES
    ]
    if not candidates:
        print(f"{args.platform} 没有需要迁移的技能。")
        return 0
    ok, fail = 0, 0
    for f in candidates:
        if f["status"] == "synced-copy":
            print(f"[跳过] {f['name']}：已是内容一致的副本，用 apply --link 转软链即可")
            continue
        try:
            r = adopt_mod.adopt(args.platform, f["name"], link=args.link, conflict="skip")
            print(f"[{r.status}] {r.slug}：{r.detail}")
            ok += 1
        except AdoptError as exc:
            print(f"[跳过] {f['name']}：{exc}")
            fail += 1
    print(f"迁移完成：采纳 {ok}，跳过/失败 {fail}。")
    return 0 if fail == 0 else 1


def cmd_apply(args: argparse.Namespace) -> int:
    actions = apply_mod.run(
        write=args.write,
        platform=args.platform,
        only=args.only,
        link=args.link,
        force=args.force,
    )
    if args.json:
        _print_json([asdict(a) for a in actions])
        return 0
    mode = "执行" if args.write else "预演（dry-run，加 --write 才落盘）"
    print(f"== apply {mode} ==")
    for a in actions:
        print(f"  [{a.platform}] {a.slug} {a.kind} — {a.detail}")
    refused = [a for a in actions if a.kind == "refused"]
    if refused:
        print(f"\n{len(refused)} 项被安全规则拒绝，请先处理后再 apply。")
        return 1
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    checks = verify_mod.run()
    if args.json:
        _print_json([asdict(c) for c in checks])
    else:
        for c in checks:
            print(f"  [{c.level}] {c.scope}/{c.name}：{c.message}")
        if not checks:
            print("全部通过：frontmatter、catalog、依赖与本机工具检查均无问题。")
    return 1 if any(c.level == "FAIL" for c in checks) else 0


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        result = resolve_mod.resolve(args.platform, args.name, winner=args.winner,
                                     dry_run=args.dry_run)
    except resolve_mod.ResolveError as exc:
        print(f"[拒绝] {exc}", file=sys.stderr)
        return 2
    print(f"[resolved] {result.slug}（{result.platform}，{result.winner} 胜出）：{result.detail}")
    return 0


def cmd_ignore(args: argparse.Namespace) -> int:
    state = load_state()
    key = f"{args.platform}/{args.name}"
    ignored = set(state.get("ignored", []))
    if args.remove:
        ignored.discard(key)
        print(f"已移出忽略名单：{key}")
    else:
        ignored.add(key)
        print(f"已加入忽略名单：{key}（doctor 不再报告；可用 --remove 恢复）")
    state["ignored"] = sorted(ignored)
    save_state(state)
    audit("ignore", slug=args.name, platform=args.platform, remove=bool(args.remove))
    return 0


def cmd_sync_status(args: argparse.Namespace) -> int:
    try:
        info = gitsync_mod.sync_status(args.remote, do_fetch=not args.offline)
    except PreflightError as exc:
        print("[拒绝] " + "；".join(exc.reasons), file=sys.stderr)
        return 2
    if args.json:
        _print_json(info)
        return 0
    print(f"仓库：{info['repo']}")
    print(f"分支：{info['branch']}　远端：{args.remote}"
          + (f"（{info['remote_url']}）" if info["remote_url"] else "（未配置）"))
    if info["has_upstream"]:
        print(f"领先远端 {info['ahead']} / 落后 {info['behind']}"
              + ("　fetch 失败，数字基于本地缓存" if not info["fetch_ok"] and info["has_remote"] else ""))
    elif info["has_remote"]:
        print("尚未建立上游跟踪（首次 push 会自动 -u）")
    print(f"待提交变更：受跟踪改动 {len(info['dirty'])}，未跟踪新文件 {len(info['untracked'])}"
          f"，已暂存 {len(info['staged'])}")
    if info["dirty"][:8]:
        print("  改动：" + "、".join(info["dirty"][:8]))
    if info["untracked"][:8]:
        print("  新增：" + "、".join(info["untracked"][:8]))
    if info["bak"]:
        print(f"  ⚠ .bak 回滚副本残留 {len(info['bak'])} 个：" + "、".join(info["bak"][:5]))
    if info["blocking"]:
        print("  ⚠ catalog 声明但 skills/ 缺失：" + "、".join(info["blocking"][:5]))
    if info["warnings"]:
        print("  · 元数据体检警告：" + "、".join(info["warnings"][:5]))
    print(f"结论：push {'可行' if info['can_push'] else '暂不可行'}；"
          f"pull {'可行' if info['can_pull'] else '暂不可行'}")
    for r in info["reasons"]:
        print("  - " + r)
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    try:
        r = gitsync_mod.push(args.remote, message=args.message)
    except PreflightError as exc:
        print("[拒绝] " + "；".join(exc.reasons), file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"[git 错误] {exc}", file=sys.stderr)
        return 3
    if r["committed"]:
        s = r["summary"]
        print(f"已提交 {r['commit']}：新增 {len(s['added'])}、更新 {len(s['updated'])}、"
              f"移除 {len(s['deleted'])} 个技能，其他 {len(s['others'])} 项")
    else:
        print("无待提交变更，直接推送。")
    if r["moved_local"]:
        print("机器本地数据已移出 git 索引（文件保留本机）：" + "、".join(r["moved_local"]))
    if r["warnings"]:
        print("提示：canonical 元数据异常（不阻断）：" + "、".join(r["warnings"][:5]))
    print(f"已推送到 {args.remote}/{r['branch']}。")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    try:
        r = gitsync_mod.pull(args.remote, rebase=args.rebase)
    except PreflightError as exc:
        print("[拒绝] " + "；".join(exc.reasons), file=sys.stderr)
        return 2
    except GitError as exc:
        print(f"[git 错误] {exc}", file=sys.stderr)
        return 3
    label = {"up-to-date": "已是最新", "fast-forward": "已快进合并",
             "rebase": "已变基"}[r["strategy"]]
    print(f"{label}（拉入 {r['behind_before']} 个提交）。")
    p = r["post_scan"]
    print(f"拉取后对账：已纳管软链 {p['managed_ok']}，待处理问题 {p['problem_total']}，"
          f"可采纳 {p['adoptable']}。")
    if r["apply_hint"]:
        print("→ " + r["apply_hint"])
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .webapp import serve
    httpd, url = serve(host="127.0.0.1", port=args.port, open_browser=not args.no_browser)
    print(f"skillsync 本机控制台：{url}")
    print("仅绑定 127.0.0.1；Ctrl+C 退出。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        httpd.server_close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillsync", description="跨平台 Agent Skill 单仓同步工具")
    parser.add_argument("--version", action="version", version=f"skillsync {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "bootstrap", help="新机器一键上手：扫描 + 下发已纳管软链 + 报告待采纳")
    p.add_argument("--dry-run", action="store_true", help="只预演，不真正创建软链")
    p.add_argument("--no-apply", action="store_true", help="跳过软链下发，只扫描并报告")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("doctor", help="只读对账")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true", help="存在分叉/失效等问题时以非零码退出（CI 用）")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("adopt", help="采纳单个平台技能进共享仓")
    p.add_argument("platform", choices=sorted(PLATFORMS))
    p.add_argument("name")
    p.add_argument("--targets", help="逗号分隔的下发平台，默认仅来源平台")
    p.add_argument("--link", action="store_true", help="采纳后立即软链下发（源目录先备份）")
    p.add_argument("--conflict", choices=["skip", "diff"], default="skip")
    p.add_argument("--from-path", help="从任意含 SKILL.md 的目录采纳（不要求源在平台目录中）")
    p.set_defaults(func=cmd_adopt)

    p = sub.add_parser("migrate", help="批量采纳某平台的未登记技能")
    p.add_argument("--from", dest="platform", choices=sorted(PLATFORMS), required=True)
    p.add_argument("--all", action="store_true", required=True, help="确认批量执行")
    p.add_argument("--link", action="store_true")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("apply", help="软链下发到各平台（默认 dry-run）")
    p.add_argument("--write", action="store_true", help="真正落盘")
    p.add_argument("--platform", choices=sorted(PLATFORMS))
    p.add_argument("--only")
    p.add_argument("--link", action="store_true", help="内容一致的实体副本也转成软链（先备份）")
    p.add_argument("--force", action="store_true", help="允许替换指向仓外的软链")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("verify", help="canonical 仓自检")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("resolve", help="分叉裁决：canonical 与平台副本二选一（先备份）")
    p.add_argument("platform", choices=sorted(PLATFORMS))
    p.add_argument("name")
    p.add_argument("--winner", choices=["canonical", "platform"], required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("ignore", help="在 doctor 中忽略某平台技能")
    p.add_argument("platform", choices=sorted(PLATFORMS))
    p.add_argument("name")
    p.add_argument("--remove", action="store_true")
    p.set_defaults(func=cmd_ignore)

    p = sub.add_parser("serve", help="启动本机 Web 控制台（仅 127.0.0.1）")
    p.add_argument("--port", type=int, default=0, help="端口，默认自动选择空闲端口")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("sync-status", help="多机同步状态：ahead/behind、待提交变更与预检结论")
    p.add_argument("--remote", default="origin")
    p.add_argument("--offline", action="store_true", help="不 fetch，只看本地缓存状态")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sync_status)

    p = sub.add_parser("push", help="预检后自动提交事实源并推送到远端")
    p.add_argument("--remote", default="origin")
    p.add_argument("--message", help="自定义提交说明（默认按变更生成中文说明）")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("pull", help="从远端拉取（默认仅快进；分叉需显式 --rebase）")
    p.add_argument("--remote", default="origin")
    p.add_argument("--rebase", action="store_true", help="本地与远端分叉时允许变基")
    p.set_defaults(func=cmd_pull)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
