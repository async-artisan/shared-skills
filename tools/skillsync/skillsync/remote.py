"""远程技能源解析与抓取（只读预览 / 确认导入共用）。

支持的 URL 形态：
- github.com/{owner}/{repo}                        → 仓库根目录
- github.com/{owner}/{repo}/tree/{ref}/{path...}   → 仓库子目录
- github.com/{owner}/{repo}/blob/{ref}/{path}      → 该文件所在目录
- github.com/{owner}/{repo}/raw/{ref}/{path...}    → 同 blob 处理
- skills.sh/...                                    → 抓页面提取 GitHub 链接后走上述流程
- 其余域名 → 明确报错，提示改为粘贴 GitHub 链接

安全约束：文件数 ≤50、单文件 ≤512KB、总量 ≤5MB、超时 20s、
只经 https；目录名合法性在落盘前由 webapp 校验。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

MAX_FILES = 50
MAX_FILE = 512 * 1024
MAX_TOTAL = 5 * 1024 * 1024
TIMEOUT = 20
_UA = {"User-Agent": "skillsync-import/0.1"}

GH_TREE_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"
    r"(?:/(?:tree|blob|raw)/([^/]+)((?:/[\w.\-/%]+)*))?/?$"
)
GH_LINK_RE = re.compile(
    r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"(?:/(?:tree|blob)/[^\s\"'<>)]+)?"
)


class RemoteError(Exception):
    """远程抓取失败（网络、限流、结构不符）。"""


def _http_get(url: str, *, accept: str = "application/json") -> bytes:
    req = urllib.request.Request(url, headers={**_UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_FILE * 4)
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise RemoteError("GitHub API 限流（未认证 60 次/小时），稍后再试") from exc
        if exc.code == 404:
            raise RemoteError("远端返回 404：链接或分支/路径不存在") from exc
        raise RemoteError(f"远端 HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RemoteError(f"网络不可达：{exc}") from exc


def _contents(owner: str, repo: str, ref: str | None, path: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
    if ref:
        url += f"?ref={urllib.request.quote(ref, safe='')}"
    data = json.loads(_http_get(url).decode("utf-8", "replace"))
    if isinstance(data, dict):  # 单文件
        return [data]
    if isinstance(data, list):
        return data
    raise RemoteError("GitHub contents 返回结构异常")


def _fetch_github(owner: str, repo: str, ref: str | None, path: str) -> dict[str, Any]:
    path = path.strip("/")
    entries: list[tuple[str, str]] = []  # (rel_path, download_url)
    total = 0

    def walk(prefix: str, depth: int) -> None:
        nonlocal total
        if depth > 4:
            raise RemoteError("目录层级过深（>4），请直接指定技能子目录")
        for item in _contents(owner, repo, ref, prefix):
            kind = item.get("type")
            rel = item.get("path", "").lstrip("/")
            if kind == "file":
                size = int(item.get("size") or 0)
                if size > MAX_FILE:
                    raise RemoteError(f"{rel} 超过单文件上限 512KB")
                total += size
                if total > MAX_TOTAL:
                    raise RemoteError("目录总量超过 5MB 上限")
                entries.append((rel, item.get("download_url") or ""))
                if len(entries) > MAX_FILES:
                    raise RemoteError(f"文件数超过 {MAX_FILES} 上限")
            elif kind == "dir":
                walk(rel, depth + 1)

    walk(path, 0)
    if not entries:
        raise RemoteError("目标目录为空")
    if not any(rel.endswith("SKILL.md") for rel, _ in entries):
        raise RemoteError("该目录没有 SKILL.md（技能目录必须包含 SKILL.md）")

    skill_md = next(rel for rel, _ in entries if rel.endswith("SKILL.md"))
    raw = _http_get(next(u for rel, u in entries if rel == skill_md),
                    accept="text/plain").decode("utf-8", "replace")
    name, desc = _parse_frontmatter(raw)
    if not name:
        name = skill_md[: -len("SKILL.md")].strip("/").split("/")[-1]
    blobs: dict[str, bytes] = {}
    for rel, dl in entries:
        blobs[rel] = _http_get(dl, accept="text/plain") if dl else b""
    files = [{"path": rel, "size": len(blobs[rel])} for rel, _ in entries]
    return {
        "source": f"https://github.com/{owner}/{repo}"
                  + (f"/tree/{ref}/{path}" if path else ""),
        "name": name,
        "description": desc,
        "files": files,
        "total_bytes": sum(len(b) for b in blobs.values()),
        "blobs": blobs,
    }


def _parse_frontmatter(md_text: str) -> tuple[str, str]:
    """从 SKILL.md frontmatter 提取 name/description，容忍缺失。"""
    lines = md_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", ""
    name = desc = ""
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            break
        m = re.match(r"^name:\s*(.+)$", line)
        if m:
            name = m.group(1).strip().strip("'\"")
        m = re.match(r"^description:\s*(.+)$", line)
        if m:
            desc = m.group(1).strip().strip("'\"")
            if desc in (">", ">-", "|", "|-"):  # 块标量：拼接后续缩进行
                buf: list[str] = []
                for cont in lines[i + 1:]:
                    if not cont.strip() or not cont[:1].isspace():
                        break
                    buf.append(cont.strip())
                desc = " ".join(buf)
    return name, desc[:200]


def _resolve_skills_sh(url: str) -> str:
    html = _http_get(url, accept="text/html").decode("utf-8", "replace")
    m = GH_LINK_RE.search(html)
    if not m:
        raise RemoteError("skills.sh 页面中未找到 GitHub 链接；请直接粘贴该技能的 GitHub 地址")
    return m.group(0)


def fetch(url: str) -> dict[str, Any]:
    """解析任意受支持的 URL → 技能目录预览（不落盘）。"""
    url = (url or "").strip()
    if not url.startswith("https://"):
        raise RemoteError("仅支持 https:// 链接")
    if "github.com/" not in url and "skills.sh/" not in url:
        raise RemoteError("当前支持 github.com 与 skills.sh；其他来源请粘贴其 GitHub 链接")
    if "skills.sh/" in url:
        url = _resolve_skills_sh(url)
    m = GH_TREE_RE.match(url)
    if not m:
        raise RemoteError("无法解析 GitHub 链接：需要仓库、/tree/{ref}/{path} 或 /blob/{ref}/{path} 形态")
    owner, repo, ref, path = m.group(1), m.group(2), m.group(3), m.group(4) or ""
    return _fetch_github(owner, repo, ref, path)
