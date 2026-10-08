"""yamllite 解析与序列化单元测试。"""
from __future__ import annotations

import unittest
import sys
from pathlib import Path

import _bootstrap  # noqa: F401 设置 SKILLSYNC_HOME，必须在 import skillsync 之前
TOOLS = Path(__file__).resolve().parents[2] / "tools" / "skillsync"
sys.path.insert(0, str(TOOLS))

from skillsync import yamllite  # noqa: E402
from skillsync.yamllite import YamlLiteError, parse, dump  # noqa: E402


class TestScalar(unittest.TestCase):
    def test_int_float(self):
        self.assertEqual(parse("k: 42")["k"], 42)
        self.assertEqual(parse("k: 3.14")["k"], 3.14)

    def test_bool_null(self):
        self.assertIs(parse("k: true")["k"], True)
        self.assertIs(parse("k: yes")["k"], True)
        self.assertIs(parse("k: false")["k"], False)
        self.assertIs(parse("k: no")["k"], False)
        self.assertIs(parse("k: null")["k"], None)
        self.assertIs(parse("k: ~")["k"], None)

    def test_quoted_strings(self):
        self.assertEqual(parse("k: \"hello\"")["k"], "hello")
        self.assertEqual(parse("k: 'world'")["k"], "world")
        # 含特殊字符的裸字符串不会被当数字解析
        self.assertEqual(parse('k: "3.14pi"')["k"], "3.14pi")

    def test_single_quote_escape(self):
        # YAML 单引号字符串里 `''` 表示一个 `'`
        self.assertEqual(parse("k: 'it''s a test'")["k"], "it's a test")

    def test_inline_list(self):
        self.assertEqual(parse("k: [a, b, c]")["k"], ["a", "b", "c"])
        self.assertEqual(parse("k: [1, 2, 3]")["k"], [1, 2, 3])
        self.assertEqual(parse("k: []")["k"], [])

    def test_empty_map(self):
        self.assertEqual(parse("k: {}")["k"], {})


class TestComments(unittest.TestCase):
    def test_strip_full_line_comment(self):
        self.assertEqual(parse("# only comment"), {})

    def test_strip_trailing_comment(self):
        self.assertEqual(parse("k: value  # trailing")["k"], "value")

    def test_comment_inside_quotes(self):
        # # 在引号内不算注释
        self.assertEqual(parse('k: "a # b"')["k"], "a # b")
        self.assertEqual(parse("k: 'a # b'")["k"], "a # b")


class TestNestedMaps(unittest.TestCase):
    def test_two_level(self):
        text = "outer:\n  inner: value\n  num: 42\n"
        out = parse(text)
        self.assertEqual(out, {"outer": {"inner": "value", "num": 42}})

    def test_list_value(self):
        text = "key:\n  - a\n  - b\n  - c\n"
        self.assertEqual(parse(text), {"key": ["a", "b", "c"]})

    def test_mixed(self):
        text = "skills:\n  alpha:\n    platforms:\n      - codex\n      - trae\n    tools_required: []\n"
        out = parse(text)
        self.assertEqual(out, {
            "skills": {"alpha": {"platforms": ["codex", "trae"],
                                 "tools_required": []}}
        })

    def test_empty_value_becomes_container(self):
        text = "skills:\n"
        self.assertEqual(parse(text), {"skills": {}})


class TestParseErrors(unittest.TestCase):
    def test_missing_colon(self):
        with self.assertRaises(YamlLiteError):
            parse("just a bare line")

    def test_list_in_non_list_context(self):
        # 列表项前必须有键值对，否则报错
        with self.assertRaises(YamlLiteError):
            parse("- a\n")


class TestDump(unittest.TestCase):
    def test_dump_simple(self):
        self.assertEqual(dump({"k": "v"}), "k: v\n")

    def test_dump_int_float_bool_null(self):
        self.assertEqual(dump({"a": 1, "b": 2.5, "c": True, "d": None}),
                         "a: 1\nb: 2.5\nc: true\nd: null\n")

    def test_dump_list(self):
        self.assertEqual(dump({"k": ["a", "b"]}), "k:\n  - a\n  - b\n")

    def test_dump_special_chars_quoted(self):
        # 含 # 或 : 的字符串需要加引号
        out = dump({"k": "a#b"})
        self.assertIn('"a#b"', out)

    def test_roundtrip(self):
        original = {"skills": {"alpha": {"platforms": ["codex", "trae"],
                                         "tools_required": []}}}
        text = dump(original)
        self.assertEqual(parse(text), original)

    def test_complex_list_item_rejected(self):
        # 复杂列表项（dict 嵌套在 list）不支持
        with self.assertRaises(YamlLiteError):
            dump({"k": [{"x": 1}]})


class TestSplitFlow(unittest.TestCase):
    def test_split_simple(self):
        self.assertEqual(yamllite._split_flow("a, b, c"), ["a", "b", "c"])

    def test_split_with_nested_brackets(self):
        self.assertEqual(yamllite._split_flow("a, [b, c], d"),
                         ["a", "[b, c]", "d"])

    def test_split_with_quoted_comma(self):
        self.assertEqual(yamllite._split_flow('"a,b", c'),
                         ['"a,b"', "c"])


if __name__ == "__main__":
    unittest.main()
