"""平台注册表：内置四平台默认值 + 用户/仓库级 platforms.yaml 覆盖。

配置查找顺序（前者存在即整体生效，不做跨文件合并）：
1. 环境变量 SKILLSYNC_PLATFORMS 指向的文件（测试/临时覆盖）
2. <home>/.skillsync/platforms.yaml（个人级，不经过仓库）
3. <repo>/registry/platforms.yaml（仓库级，可随 fork 提交共享）
4. 内置默认：codex / claude / workbuddy / trae（零配置行为不变）

配置 schema（yamllite 子集：map 嵌套，按文件顺序保序）：

    version: 1
    platforms:
      qwen:
        label: 千问办公
        skills_dir: ~/.qwen-office/skills
        builtin_dirnames: [.system]

边界：本机制只适用于「本地目录 + SKILL.md + 接受软链」的平台；
云端市场/打包上传类通道需另行扩展 apply 的下发方式。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import yamllite

# 平台 key 同时进入 catalog、CSS 类名与命令行，严格限定为安全短名
_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_WIN_VAR_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")
_DIRNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

MAX_LABEL_LEN = 40
MAX_PATH_LEN = 300
MAX_PLATFORMS = 16

USER_CONFIG_DIR = ".skillsync"
CONFIG_NAME = "platforms.yaml"
ENV_CONFIG_PATH = "SKILLSYNC_PLATFORMS"


class PlatformConfigError(ValueError):
    """platforms.yaml 结构或字段不合法。"""


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    skills_dir: Path
    # 相对于 skills_dir 需要整体跳过的内置目录名
    builtin_dirnames: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlatformsConfig:
    platforms: dict[str, Platform]
    # default / repo / user / env
    source: str
    path: Path | None


# (key, label, 相对 home 的目录, 内置子目录)
_DEFAULT_SPECS: list[tuple[str, str, str, frozenset[str]]] = [
    ("codex", "Codex", str(Path(".codex") / "skills"), frozenset({".system"})),
    ("claude", "Claude", str(Path(".claude") / "skills"), frozenset()),
    ("workbuddy", "WorkBuddy", str(Path(".workbuddy") / "skills"), frozenset()),
    ("trae", "TRAE", str(Path(".trae-cn") / "skills"), frozenset()),
]


def _default_platforms(home: Path) -> dict[str, Platform]:
    return {
        key: Platform(key=key, label=label,
                      skills_dir=home / rel, builtin_dirnames=builtin)
        for key, label, rel, builtin in _DEFAULT_SPECS
    }


def _expand_vars(text: str, environ: Mapping[str, str]) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        return environ.get(name, m.group(0))

    text = _VAR_RE.sub(repl, text)
    text = _WIN_VAR_RE.sub(lambda m: environ.get(m.group(1), m.group(0)), text)
    return text


def _expand_path(raw: str, home: Path, environ: Mapping[str, str],
                 repo_root: Path) -> Path:
    s = _expand_vars(raw.strip(), environ)
    if s.startswith("~"):
        s = str(home) + s[1:]
    p = Path(s)
    if not p.is_absolute():
        p = repo_root / p
    return p


def _validate_dirnames(value: Any, key: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PlatformConfigError(f"平台 {key}：builtin_dirnames 必须是字符串列表")
    names = set()
    for v in value:
        name = v.strip()
        if name in (".", "..") or not _DIRNAME_RE.match(name):
            raise PlatformConfigError(f"平台 {key}：非法内置目录名 {v!r}")
        names.add(name)
    return frozenset(names)


def _parse_config(text: str, path: Path, home: Path,
                  environ: Mapping[str, str], repo_root: Path) -> dict[str, Platform]:
    try:
        data = yamllite.parse(text)
    except yamllite.YamlLiteError as exc:
        raise PlatformConfigError(f"{path}：YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise PlatformConfigError(f"{path}：顶层结构必须是 map")
    platforms = data.get("platforms")
    if not isinstance(platforms, dict) or not platforms:
        raise PlatformConfigError(f"{path}：platforms 必须是非空 map")
    if len(platforms) > MAX_PLATFORMS:
        raise PlatformConfigError(
            f"{path}：平台数量超过上限 {MAX_PLATFORMS}")

    result: dict[str, Platform] = {}
    for key, spec in platforms.items():
        if not isinstance(key, str) or not _KEY_RE.match(key):
            raise PlatformConfigError(
                f"{path}：平台 key 只能是小写字母/数字/短横且不以短横开头"
                f"结尾（如 qwen-office），得到 {key!r}")
        if not isinstance(spec, dict):
            raise PlatformConfigError(f"{path}：平台 {key} 的定义必须是 map")
        label = spec.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PlatformConfigError(f"{path}：平台 {key} 缺少非空 label")
        label = label.strip()
        if len(label) > MAX_LABEL_LEN:
            raise PlatformConfigError(
                f"{path}：平台 {key} 的 label 超过 {MAX_LABEL_LEN} 字符")
        skills_raw = spec.get("skills_dir")
        if not isinstance(skills_raw, str) or not skills_raw.strip():
            raise PlatformConfigError(f"{path}：平台 {key} 缺少非空 skills_dir")
        if len(skills_raw) > MAX_PATH_LEN:
            raise PlatformConfigError(
                f"{path}：平台 {key} 的 skills_dir 过长")
        builtin = _validate_dirnames(spec.get("builtin_dirnames"), key)
        result[key] = Platform(
            key=key, label=label,
            skills_dir=_expand_path(skills_raw, home, environ, repo_root),
            builtin_dirnames=builtin,
        )
    return result


def resolve_platforms(
    registry_dir: Path,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> PlatformsConfig:
    """按优先级解析生效的平台注册表。

    registry_dir 即 REPO_ROOT/registry；仓库级配置与其父目录锚定相对路径。
    environ/home 可注入以便测试；默认取进程环境与 Path.home()。
    """
    env = environ if environ is not None else os.environ
    home_dir = home if home is not None else Path.home()
    repo_root = registry_dir.parent

    env_path = env.get(ENV_CONFIG_PATH)
    if env_path:
        p = Path(env_path).expanduser()
        if not p.is_file():
            raise PlatformConfigError(
                f"环境变量 {ENV_CONFIG_PATH} 指向的配置不存在：{p}")
        platforms = _parse_config(
            p.read_text(encoding="utf-8"), p, home_dir, env, repo_root)
        return PlatformsConfig(platforms, "env", p)

    user_path = home_dir / USER_CONFIG_DIR / CONFIG_NAME
    if user_path.is_file():
        platforms = _parse_config(
            user_path.read_text(encoding="utf-8"),
            user_path, home_dir, env, repo_root)
        return PlatformsConfig(platforms, "user", user_path)

    repo_path = registry_dir / CONFIG_NAME
    if repo_path.is_file():
        platforms = _parse_config(
            repo_path.read_text(encoding="utf-8"),
            repo_path, home_dir, env, repo_root)
        return PlatformsConfig(platforms, "repo", repo_path)

    return PlatformsConfig(_default_platforms(home_dir), "default", None)
