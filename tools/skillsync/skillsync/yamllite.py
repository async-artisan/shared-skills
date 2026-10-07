"""极简 YAML 子集解析/序列化器。

支持的语法（刻意保持小而确定）：
- 键值对 `key: value`，按缩进组织嵌套 map
- `- item` 标量列表
- 行内列表 `[a, b, c]`
- 标量：整数、浮点、true/false/null、单/双引号字符串、裸字符串
- # 注释、空行

catalog.yaml / state.yaml 由本工具读写并保持固定风格；
遇到无法解析的结构会明确报错而不是静默丢弃。
"""
from __future__ import annotations

from typing import Any


class YamlLiteError(ValueError):
    pass


def _strip_comment(line: str) -> str:
    in_single = in_double = False
    out = []
    for ch in line:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_flow(inner: str) -> list[str]:
    parts, depth, buf, quote = [], 0, [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "":
        return None
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        return [_parse_scalar(p) for p in _split_flow(inner)] if inner else []
    if s == "{}":
        return {}
    return s


class _LazyContainer(dict):
    """空值键的占位容器：首个子节点决定它最终是 dict 还是 list。"""

    def __init__(self, stack: list[tuple[int, Any]], indent: int, parent: dict, key: str):
        super().__init__()
        self._stack = stack
        self._indent = indent
        self._parent = parent
        self._key = key
        stack.append((indent, self))

    def attach_list(self) -> list:
        lst: list = []
        self._parent[self._key] = lst
        self._stack[-1] = (self._indent, lst)
        return lst


def _materialize(node: Any) -> Any:
    if isinstance(node, dict) and not isinstance(node, _LazyContainer):
        return {k: _materialize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_materialize(v) for v in node]
    return node


def parse(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()

        is_item = body.startswith("-")
        while len(stack) > 1 and indent <= stack[-1][0]:
            # 允许列表项与所属键/上一列表项同缩进（标准 YAML 写法）
            if is_item and indent == stack[-1][0] and (
                isinstance(stack[-1][1], _LazyContainer) or isinstance(stack[-1][1], list)
            ):
                break
            stack.pop()
        parent = stack[-1][1]

        if is_item:
            item_raw = body[1:].strip()
            if isinstance(parent, _LazyContainer):
                parent = parent.attach_list()
            if not isinstance(parent, list):
                raise YamlLiteError(f"第 {lineno} 行：列表项出现在非列表上下文中")
            if item_raw:
                parent.append(_parse_scalar(item_raw))
            continue

        if ":" not in body:
            raise YamlLiteError(f"第 {lineno} 行：无法解析：{body}")
        key, _, value_text = body.partition(":")
        key, value_text = key.strip(), value_text.strip()
        if not isinstance(parent, dict):
            raise YamlLiteError(f"第 {lineno} 行：键值对出现在列表上下文中")
        if value_text == "":
            parent[key] = _LazyContainer(stack, indent, parent, key)
        else:
            parent[key] = _parse_scalar(value_text)

    return _materialize(root)


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if s == "" or any(ch in s for ch in ":#'\"\n") or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def dump(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def emit(value: Any, indent: int) -> None:
        pad = " " * indent
        if isinstance(value, dict):
            for key, val in value.items():
                if isinstance(val, dict):
                    lines.append(f"{pad}{key}: {{}}" if not val else f"{pad}{key}:")
                    if val:
                        emit(val, indent + 2)
                elif isinstance(val, list):
                    lines.append(f"{pad}{key}: []" if not val else f"{pad}{key}:")
                    if val:
                        emit(val, indent + 2)
                else:
                    lines.append(f"{pad}{key}: {_dump_scalar(val)}")
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    raise YamlLiteError("本工具的 YAML 子集不支持复杂列表项")
                lines.append(f"{pad}- {_dump_scalar(item)}")

    emit(data, 0)
    return "\n".join(lines) + "\n"
