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

import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

MAX_FILES = 50
MAX_FILE = 512 * 1024
MAX_TOTAL = 5 * 1024 * 1024
TIMEOUT = 20
_UA = {"User-Agent": "skillsync-import/0.1"}
_API_HOSTS = frozenset({"api.github.com"})
_DOWNLOAD_HOSTS = frozenset({"raw.githubusercontent.com", "github.com", "www.github.com",
                             "objects.githubusercontent.com"})
_SKILLS_SH_HOSTS = frozenset({"skills.sh", "www.skills.sh"})
_ALL_HOSTS = _API_HOSTS | _DOWNLOAD_HOSTS | _SKILLS_SH_HOSTS

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


class _AllowedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(url: str, allowed_hosts: frozenset[str]) -> None:
    if not isinstance(url, str) or not url:
        raise RemoteError("远程下载地址不合法")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RemoteError("远程地址端口不合法") from exc
    if (parsed.scheme != "https" or not host or host not in allowed_hosts
            or port not in (None, 443) or parsed.username or parsed.password):
        raise RemoteError(f"远程地址不在允许范围内：{url}")


def _http_get(url: str, *, accept: str = "application/json",
              max_bytes: int = MAX_FILE * 4,
              allowed_hosts: frozenset[str] | None = None) -> bytes:
    if allowed_hosts is None:
        allowed_hosts = _ALL_HOSTS
    if allowed_hosts is not None:
        _validate_url(url, allowed_hosts)
    req = urllib.request.Request(url, headers={**_UA, "Accept": accept})
    try:
        opener = urllib.request.build_opener(_AllowedRedirectHandler(allowed_hosts))
        with opener.open(req, timeout=TIMEOUT) as resp:
            final_url = resp.geturl() if hasattr(resp, "geturl") else url
            if allowed_hosts is not None:
                _validate_url(final_url, allowed_hosts)
            headers = getattr(resp, "headers", None)
            content_length = headers.get("Content-Length") if headers else None
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise RemoteError(f"远程响应超过大小上限 {max_bytes} 字节")
                except ValueError as exc:
                    raise RemoteError("远程响应 Content-Length 不合法") from exc
            chunks: list[bytes] = []
            total = 0
            while total <= max_bytes:
                chunk = resp.read(min(64 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > max_bytes:
                raise RemoteError(f"远程响应超过大小上限 {max_bytes} 字节")
            return b"".join(chunks)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise RemoteError("远程地址发生重定向，已拒绝以避免跨主机下载") from exc
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
        url += f"?ref={quote(ref, safe='')}"
    data = json.loads(_http_get(url, allowed_hosts=_API_HOSTS).decode("utf-8", "replace"))
    if isinstance(data, dict):  # 单文件
        return [data]
    if isinstance(data, list):
        return data
    raise RemoteError("GitHub contents 返回结构异常")


def _fetch_github(owner: str, repo: str, ref: str | None, path: str) -> dict[str, Any]:
    path = path.strip("/")
    entries: list[tuple[str, str, int]] = []  # (relative path, download_url, declared size)
    total = 0

    def relative_path(full: str) -> str:
        if not isinstance(full, str):
            raise RemoteError("远端返回缺少合法文件路径")
        normalized = PurePosixPath(full)
        if (not full or "\\" in full or normalized.is_absolute()
                or any(part in ("", ".", "..") for part in full.split("/"))):
            raise RemoteError(f"远端返回非法路径：{full}")
        prefix = path.rstrip("/") + "/" if path else ""
        if prefix and full.startswith(prefix):
            return full[len(prefix):]
        if path and full == path:
            return Path(full).name
        if prefix:
            raise RemoteError(f"远端文件路径超出所选目录：{full}")
        return full

    def walk(prefix: str, depth: int) -> None:
        nonlocal total
        if depth > 4:
            raise RemoteError("目录层级过深（>4），请直接指定技能子目录")
        for item in _contents(owner, repo, ref, prefix):
            kind = item.get("type")
            full = item.get("path", "")
            rel = relative_path(full)
            if kind == "file":
                try:
                    size = int(item.get("size") or 0)
                except (TypeError, ValueError) as exc:
                    raise RemoteError(f"{rel} 的远端文件大小不合法") from exc
                if size < 0:
                    raise RemoteError(f"{rel} 的远端文件大小不合法")
                if size > MAX_FILE:
                    raise RemoteError(f"{rel} 超过单文件上限 512KB")
                total += size
                if total > MAX_TOTAL:
                    raise RemoteError("目录总量超过 5MB 上限")
                entries.append((rel, item.get("download_url") or "", size))
                if len(entries) > MAX_FILES:
                    raise RemoteError(f"文件数超过 {MAX_FILES} 上限")
            elif kind == "dir":
                walk(full, depth + 1)

    walk(path, 0)
    if not entries:
        raise RemoteError("目标目录为空")
    root_skills = [item for item in entries if item[0] == "SKILL.md"]
    if len(root_skills) != 1:
        raise RemoteError("技能目录必须包含唯一的根 SKILL.md")
    if any(item[0] != "SKILL.md" and item[0].endswith("/SKILL.md") for item in entries):
        raise RemoteError("技能目录不能包含嵌套的 SKILL.md")

    skill_md, skill_url, _ = root_skills[0]
    if not skill_url:
        raise RemoteError("SKILL.md 缺少可下载地址")
    blobs: dict[str, bytes] = {}
    actual_total = 0
    for rel, dl, _ in entries:
        if not dl:
            raise RemoteError(f"远端文件缺少可下载地址：{rel}")
        remaining = MAX_TOTAL - actual_total
        blob = _http_get(dl, accept="text/plain",
                         max_bytes=min(MAX_FILE, remaining),
                         allowed_hosts=_DOWNLOAD_HOSTS)
        if len(blob) > MAX_FILE or len(blob) > remaining:
            raise RemoteError("下载内容超过单文件或总量上限")
        blobs[rel] = blob
        actual_total += len(blobs[rel])
    raw = blobs[skill_md].decode("utf-8", "replace")
    name, desc = _parse_frontmatter(raw)
    if not name:
        name = skill_md[: -len("SKILL.md")].strip("/").split("/")[-1]
    files = [{"path": rel, "size": len(blobs[rel]),
              "sha256": hashlib.sha256(blobs[rel]).hexdigest()}
             for rel, _, _ in entries]
    preview = {
        "source": f"https://github.com/{owner}/{repo}"
                  + (f"/tree/{ref}/{path}" if path else ""),
        "name": name,
        "description": desc,
        "files": files,
        "total_bytes": actual_total,
        "blobs": blobs,
    }
    preview["binding"] = preview_binding(preview)
    return preview


def preview_binding(preview: dict[str, Any]) -> str:
    """返回绑定远程预览内容的摘要，供确认请求防止内容漂移。"""
    files = [
        {"path": str(item.get("path") or ""),
         "size": int(item.get("size") or 0),
         "sha256": str(item.get("sha256") or "")}
        for item in preview.get("files", [])
    ]
    files.sort(key=lambda item: item["path"])
    material = {
        "source": str(preview.get("source") or ""),
        "name": str(preview.get("name") or ""),
        "files": files,
    }
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    html = _http_get(url, accept="text/html", allowed_hosts=_SKILLS_SH_HOSTS).decode("utf-8", "replace")
    m = GH_LINK_RE.search(html)
    if not m:
        raise RemoteError("skills.sh 页面中未找到 GitHub 链接；请直接粘贴该技能的 GitHub 地址")
    return m.group(0)


def fetch(url: str) -> dict[str, Any]:
    """解析任意受支持的 URL → 技能目录预览（不落盘）。"""
    url = (url or "").strip()
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise RemoteError("仅支持 https:// 链接")
    if host not in {"github.com", "www.github.com", "skills.sh", "www.skills.sh"}:
        raise RemoteError("当前支持 github.com 与 skills.sh；其他来源请粘贴其 GitHub 链接")
    if host in _SKILLS_SH_HOSTS:
        url = _resolve_skills_sh(url)
    m = GH_TREE_RE.match(url)
    if not m:
        raise RemoteError("无法解析 GitHub 链接：需要仓库、/tree/{ref}/{path} 或 /blob/{ref}/{path} 形态")
    owner, repo, ref, path = m.group(1), m.group(2), m.group(3), m.group(4) or ""
    if any(part in (".", "..") for part in PurePosixPath(path).parts):
        raise RemoteError("GitHub 路径包含非法目录段")
    return _fetch_github(owner, repo, ref, path)
