"""catalog / state 的读写与审计日志。"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import yamllite
from .core import AUDIT_PATH, CATALOG_EXAMPLE_PATH, CATALOG_PATH, REGISTRY_DIR, STATE_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_yaml(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    data = yamllite.parse(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层结构必须是 map")
    return data


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 原子写入：先写临时文件再 rename，避免写一半崩溃损坏唯一事实源
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yamllite.dump(data), encoding="utf-8")
    tmp.replace(path)


def ensure_catalog() -> None:
    """catalog.yaml 缺失时从 catalog.example.yaml 复制初始化。

    catalog.yaml 是私有数据（被 .gitignore 排除），新机器 clone 公开仓后首次
    运行命令时自动得到一份空台账模板；example 也不存在时保持无文件状态，
    load_catalog 会回退到内存空结构。
    """
    if CATALOG_PATH.exists() or not CATALOG_EXAMPLE_PATH.exists():
        return
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CATALOG_EXAMPLE_PATH, CATALOG_PATH)


def load_catalog() -> dict[str, Any]:
    ensure_catalog()
    data = _load_yaml(CATALOG_PATH, {"version": 1, "skills": {}})
    data.setdefault("version", 1)
    data.setdefault("skills", {})
    if not isinstance(data["skills"], dict):
        raise ValueError("catalog.yaml 的 skills 必须是 map")
    return data


def save_catalog(data: dict[str, Any]) -> None:
    _save_yaml(CATALOG_PATH, data)


def load_state() -> dict[str, Any]:
    data = _load_yaml(STATE_PATH, {"version": 1, "ignored": [], "last_apply": {}})
    data.setdefault("ignored", [])
    data.setdefault("last_apply", {})
    return data


def save_state(data: dict[str, Any]) -> None:
    _save_yaml(STATE_PATH, data)


def audit(action: str, slug: str = "", platform: str = "", **detail: Any) -> None:
    """向 registry/audit.logl 追加一行结构化操作记录。"""
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "time": _now_iso(),
        "action": action,
        "slug": slug,
        "platform": platform,
        **detail,
    }
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
