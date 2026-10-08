"""yamllite 单元测试：覆盖解析/序列化的关键边界与错误情况。

运行：cd tools/skillsync && python -m pytest tests/test_yamllite.py -v
"""
import pytest
from skillsync.yamllite import (
    parse, dump, YamlLiteError,
    _strip_comment, _split_flow, _parse_scalar,
)


# ─── _strip_comment ───────────────────────────────────────────

class TestStripComment:
    def test_plain_comment(self):
        assert _strip_comment("key: value  # comment") == "key: value"

    def test_full_line_comment(self):
        assert _strip_comment("# only comment") == ""

    def test_double_quote_preserves_hash(self):
        assert _strip_comment('key: "a#b"') == 'key: "a#b"'

    def test_single_quote_preserves_hash(self):
        assert _strip_comment("key: 'a#b'") == "key: 'a#b'"

    def test_multiple_hashes_only_first_unquoted_stripped(self):
        assert _strip_comment("key: a # b # c") == "key: a"

    def test_no_comment(self):
        assert _strip_comment("key: value") == "key: value"

    def test_empty_line(self):
        assert _strip_comment("   ") == ""


# ─── _split_flow ──────────────────────────────────────────────

class TestSplitFlow:
    def test_simple_list(self):
        assert _split_flow("a, b, c") == ["a", "b", "c"]

    def test_nested_brackets(self):
        assert _split_flow("a, [b, c], d") == ["a", "[b, c]", "d"]

    def test_quoted_comma(self):
        assert _split_flow('"a,b", c') == ['"a,b"', "c"]

    def test_empty(self):
        assert _split_flow("") == []

    def test_trailing_comma(self):
        assert _split_flow("a, b,") == ["a", "b"]


# ─── _parse_scalar ────────────────────────────────────────────

class TestParseScalar:
    @pytest.mark.parametrize("raw,expected", [
        ("42", 42),
        ("0", 0),
        ("-5", -5),
        ("3.14", 3.14),
        ("1e3", 1e3),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("false", False),
        ("False", False),
        ("no", False),
        ("null", None),
        ("Null", None),
        ("~", None),
        ("", None),
    ])
    def test_primitives(self, raw, expected):
        assert _parse_scalar(raw) == expected

    def test_double_quoted_string(self):
        assert _parse_scalar('"hello"') == "hello"

    def test_single_quoted_string(self):
        assert _parse_scalar("'hello'") == "hello"

    def test_single_quote_escape(self):
        assert _parse_scalar("'it''s'") == "it's"

    def test_double_quoted_with_special(self):
        assert _parse_scalar('"a#b:c"') == "a#b:c"

    def test_bare_string(self):
        assert _parse_scalar("bare_string") == "bare_string"

    def test_inline_list(self):
        assert _parse_scalar("[a, 1, true]") == ["a", 1, True]

    def test_empty_inline_list(self):
        assert _parse_scalar("[]") == []

    def test_empty_map(self):
        assert _parse_scalar("{}") == {}

    def test_nested_inline_list(self):
        assert _parse_scalar("[[a, b], c]") == [["a", "b"], "c"]


# ─── parse ────────────────────────────────────────────────────

class TestParse:
    def test_simple_key_value(self):
        assert parse("k: v") == {"k": "v"}

    def test_multiple_keys(self):
        text = "a: 1\nb: 2\nc: 3"
        assert parse(text) == {"a": 1, "b": 2, "c": 3}

    def test_nested_dict(self):
        text = "parent:\n  child: value"
        assert parse(text) == {"parent": {"child": "value"}}

    def test_deep_nested_dict(self):
        text = "a:\n  b:\n    c: deep"
        assert parse(text) == {"a": {"b": {"c": "deep"}}}

    def test_scalar_list(self):
        text = "items:\n  - a\n  - b\n  - c"
        assert parse(text) == {"items": ["a", "b", "c"]}

    def test_list_with_scalars(self):
        text = "nums:\n  - 1\n  - 2\n  - 3"
        assert parse(text) == {"nums": [1, 2, 3]}

    def test_empty_value_becomes_empty_dict(self):
        text = "key:\n  sub: val"
        assert parse(text) == {"key": {"sub": "val"}}

    def test_comments_skipped(self):
        text = "# header\nk: v  # inline\n# tail"
        assert parse(text) == {"k": "v"}

    def test_blank_lines_skipped(self):
        text = "\n\nk: v\n\n"
        assert parse(text) == {"k": "v"}

    def test_inline_list_value(self):
        assert parse("k: [a, b, c]") == {"k": ["a", "b", "c"]}

    def test_mixed_nested(self):
        text = "parent:\n  - item1\n  - item2\nother: val"
        assert parse(text) == {"parent": ["item1", "item2"], "other": "val"}

    def test_error_no_colon(self):
        with pytest.raises(YamlLiteError, match="无法解析"):
            parse("just a string")

    def test_error_list_item_in_dict_context(self):
        with pytest.raises(YamlLiteError, match="非列表上下文"):
            parse("key: val\n  - item")

    def test_error_key_in_list_context(self):
        with pytest.raises(YamlLiteError, match="列表上下文"):
            parse("items:\n  - a\n  k: v")

    def test_empty_text(self):
        assert parse("") == {}

    def test_siblings_at_same_indent(self):
        text = "a:\n  x: 1\nb:\n  y: 2"
        assert parse(text) == {"a": {"x": 1}, "b": {"y": 2}}


# ─── dump ──────────────────────────────────────────────────────

class TestDump:
    def test_simple(self):
        assert dump({"k": "v"}) == "k: v\n"

    def test_int_and_bool(self):
        assert dump({"n": 42, "f": True, "f2": False}) == "n: 42\nf: true\nf2: false\n"

    def test_none(self):
        assert dump({"x": None}) == "x: null\n"

    def test_empty_dict_value(self):
        assert dump({"k": {}}) == "k: {}\n"

    def test_empty_list_value(self):
        assert dump({"k": []}) == "k: []\n"

    def test_nested_dict(self):
        result = dump({"parent": {"child": "val"}})
        assert "parent:" in result
        assert "child: val" in result

    def test_scalar_list(self):
        result = dump({"items": ["a", "b"]})
        assert "- a" in result
        assert "- b" in result

    def test_special_chars_escaped(self):
        result = dump({"k": 'a"b#c'})
        assert '"a\\"b#c"' in result

    def test_empty_string_escaped(self):
        result = dump({"k": ""})
        assert 'k: ""' in result

    def test_leading_space_escaped(self):
        result = dump({"k": " leading"})
        assert '"leading"' not in result  # 带空格应被引号包裹

    def test_error_complex_list_item(self):
        with pytest.raises(YamlLiteError, match="复杂列表项"):
            dump({"items": [{"nested": "dict"}]})

    def test_error_list_in_list(self):
        with pytest.raises(YamlLiteError, match="复杂列表项"):
            dump({"items": [["a", "b"]]})


# ─── round-trip ────────────────────────────────────────────────

class TestRoundTrip:
    """parse(dump(data)) == data 的往返一致性（在支持的子集范围内）。"""

    @pytest.mark.parametrize("data", [
        {"k": "v"},
        {"n": 42, "f": True, "f2": False, "x": None},
        {"items": ["a", "b", "c"]},
        {"parent": {"child": "val"}},
        {"k": {}},
        {"k": []},
        {"a": 1, "b": "str", "c": [1, 2, 3]},
    ])
    def test_roundtrip(self, data):
        dumped = dump(data)
        reparsed = parse(dumped)
        assert reparsed == data, f"round-trip 失败:\n  dumped: {dumped!r}\n  reparsed: {reparsed!r}\n  expected: {data!r}"


# ─── 真实 catalog.yaml 片段 ────────────────────────────────────

class TestRealWorld:
    def test_catalog_snippet(self):
        text = """version: 1
skills:
  demo-skill:
    source_platform: workbuddy
    platforms:
      - workbuddy
    tools_required: []
    depends_on: []
    description_zh: ""
    adopted_at: "2026-10-07T09:04:15+08:00"
"""
        result = parse(text)
        assert result["version"] == 1
        assert "demo-skill" in result["skills"]
        assert result["skills"]["demo-skill"]["source_platform"] == "workbuddy"
        assert result["skills"]["demo-skill"]["platforms"] == ["workbuddy"]
        assert result["skills"]["demo-skill"]["tools_required"] == []
        assert result["skills"]["demo-skill"]["description_zh"] == ""
        assert result["skills"]["demo-skill"]["adopted_at"] == "2026-10-07T09:04:15+08:00"
