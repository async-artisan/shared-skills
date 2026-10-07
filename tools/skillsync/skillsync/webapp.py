"""skillsync 本机 Web 控制台。

设计约束：
- 仅 Python 标准库（http.server），仅绑定 127.0.0.1，无登录体系；
- 不引入第二状态源：所有数据实时来自 doctor/adopt/resolve/apply/store；
- 页面不提供在线编辑，只做只读看板 + 受控动作触发，写操作由底层模块保证
  “默认 dry-run / 先备份 / 留审计”。
"""
from __future__ import annotations

import contextlib
import difflib
import io
import json
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import adopt as adopt_mod
from . import apply as apply_mod
from . import resolve as resolve_mod
from .core import PLATFORMS, SKILL_MD, SKILLS_DIR, hash_skill, scan_platform
from .doctor import PROBLEM_STATUSES, scan_all
from .store import load_state, save_state, audit
from .adopt import AdoptError
from .resolve import ResolveError

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


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


def _skill_file(slug: str) -> dict[str, str]:
    from .store import load_catalog
    if slug not in load_catalog().get("skills", {}):
        raise ApiError("技能未登记 catalog", 404)
    path = SKILLS_DIR / slug / SKILL_MD
    if not path.is_file():
        raise ApiError("canonical 缺少 SKILL.md", 404)
    return {"slug": slug, "content": path.read_text(encoding="utf-8", errors="replace")}


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
    actions = apply_mod.run(
        write=bool(body.get("write")),
        platform=body.get("platform") or None,
        only=body.get("only") or None,
        link=bool(body.get("link")),
        force=bool(body.get("force")),
    )
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
    if body.get("remove"):
        ignored.discard(key)
    else:
        ignored.add(key)
    state["ignored"] = sorted(ignored)
    save_state(state)
    audit("ignore", slug=name, platform=platform, remove=bool(body.get("remove")), via="web")
    return {"ok": True, "ignored": sorted(ignored)}


class Handler(BaseHTTPRequestHandler):
    server_version = "skillsync/0.1"

    # 静默默认访问日志；动作审计在业务层落 audit.logl
    def log_message(self, fmt: str, *args: Any) -> None:
        return

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
            if parsed.path == "/api/scan":
                state = load_state()
                result = scan_all()
                result["state"] = {"ignored": state.get("ignored", []),
                                   "last_apply": state.get("last_apply", {})}
                result["problem_total"] = sum(
                    result["summary"].get(k, 0) for k in PROBLEM_STATUSES)
                self._send_json(result)
                return
            if parsed.path == "/api/diff":
                q = parse_qs(parsed.query)
                self._send_json(_diff_skill(q.get("platform", [""])[0],
                                            q.get("name", [""])[0]))
                return
            if parsed.path == "/api/skill":
                q = parse_qs(parsed.query)
                self._send_json(_skill_file(q.get("slug", [""])[0]))
                return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except ApiError as exc:
            self._send_json({"ok": False, "error": str(exc)}, exc.status)
        except Exception as exc:  # 单用户本机工具：完整错误回传便于排障
            self._send_json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            routes = {
                "/api/adopt": _do_adopt,
                "/api/resolve": _do_resolve,
                "/api/apply": _do_apply,
                "/api/ignore": _do_ignore,
            }
            handler = routes.get(parsed.path)
            if handler is None:
                self._send_json({"ok": False, "error": "not found"}, 404)
                return
            self._send_json(handler(body))
        except (ApiError, AdoptError, ResolveError) as exc:
            status = exc.status if isinstance(exc, ApiError) else 400
            self._send_json({"ok": False, "error": str(exc)}, status)
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
