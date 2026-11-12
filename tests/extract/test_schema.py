"""export/manifest.schema.json: the examples validate, and the schema matches the tool."""

import copy
import json
import re
import unittest

from .support import EXAMPLE_MANIFEST, HANDLED_MANIFEST, SCHEMA, ex

_ANNOTATIONS = {"$schema", "title", "description", "$defs", "default"}
_SUPPORTED = _ANNOTATIONS | {
    "type", "const", "enum", "pattern", "minLength", "minItems", "uniqueItems", "properties", "required",
    "additionalProperties", "propertyNames", "items", "oneOf", "allOf", "not", "$ref", "dependentRequired",
}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


class MiniValidator(object):
    """The JSON Schema keywords this schema uses, and nothing else.

    An unknown keyword raises, so a typo in the schema can't be skipped
    silently. Patterns use Python ``re``, which agrees with ECMA-262 on the
    constructs the schema uses (classes, groups, lookaheads, anchors).
    """

    def __init__(self, root):
        self.root = root

    def errors(self, instance, schema=None, where="$"):
        schema = self.root if schema is None else schema
        unknown = set(schema) - _SUPPORTED
        if unknown:
            raise AssertionError("unsupported schema keywords at %s: %s" % (where, sorted(unknown)))
        out = []
        if "$ref" in schema:
            target = self.root
            for part in schema["$ref"].lstrip("#/").split("/"):
                target = target[part]
            out += self.errors(instance, target, where)
        kind = schema.get("type")
        if kind and not (isinstance(instance, _TYPES[kind]) and not (kind != "boolean" and isinstance(instance, bool))):
            return out + ["%s: expected %s" % (where, kind)]
        if "const" in schema and instance != schema["const"]:
            out.append("%s: expected %r" % (where, schema["const"]))
        if "enum" in schema and instance not in schema["enum"]:
            out.append("%s: %r not in enum" % (where, instance))
        if isinstance(instance, str):
            if len(instance) < schema.get("minLength", 0):
                out.append("%s: too short" % where)
            if "pattern" in schema and not re.search(schema["pattern"], instance):
                out.append("%s: %r doesn't match %s" % (where, instance, schema["pattern"]))
        if isinstance(instance, list):
            if len(instance) < schema.get("minItems", 0):
                out.append("%s: too few items" % where)
            if schema.get("uniqueItems") and len({json.dumps(i, sort_keys=True) for i in instance}) != len(instance):
                out.append("%s: items not unique" % where)
            if "items" in schema:
                for i, item in enumerate(instance):
                    out += self.errors(item, schema["items"], "%s[%d]" % (where, i))
        if isinstance(instance, dict):
            for key in schema.get("required", []):
                if key not in instance:
                    out.append("%s: missing %r" % (where, key))
            for key, needs in schema.get("dependentRequired", {}).items():
                if key in instance:
                    out += ["%s: %r needs %r" % (where, key, n) for n in needs if n not in instance]
            properties = schema.get("properties", {})
            for key, value in instance.items():
                if "propertyNames" in schema:
                    out += self.errors(key, schema["propertyNames"], "%s.<key %r>" % (where, key))
                if key in properties:
                    out += self.errors(value, properties[key], "%s.%s" % (where, key))
                elif schema.get("additionalProperties") is False:
                    out.append("%s: unexpected key %r" % (where, key))
                elif isinstance(schema.get("additionalProperties"), dict):
                    out += self.errors(value, schema["additionalProperties"], "%s.%s" % (where, key))
        for sub in schema.get("allOf", []):
            out += self.errors(instance, sub, where)
        if "oneOf" in schema:
            passing = sum(1 for sub in schema["oneOf"] if not self.errors(instance, sub, where))
            if passing != 1:
                out.append("%s: matches %d of the oneOf branches, not exactly 1" % (where, passing))
        if "not" in schema and not self.errors(instance, schema["not"], where):
            out.append("%s: matches a 'not' schema" % where)
        return out


def load(path):
    with open(str(path), encoding="utf-8") as handle:
        return json.load(handle)


class SchemaValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load(SCHEMA)
        cls.validator = MiniValidator(cls.schema)
        cls.example = load(EXAMPLE_MANIFEST)

    def test_example_manifests_validate(self):
        for path in (EXAMPLE_MANIFEST, HANDLED_MANIFEST):
            with self.subTest(manifest=path.name):
                self.assertEqual(self.validator.errors(load(path)), [])

    def test_schema_rejects_what_the_tool_rejects(self):
        def broken(change):
            manifest = copy.deepcopy(self.example)
            change(manifest)
            return manifest

        cases = {
            "unknown top-level key": lambda m: m.update(extra=1),
            "integer schema_version": lambda m: m.update(schema_version=1),
            "target under internal/": lambda m: m["files"][0].update(to="internal/x.md"),
            "target in .git": lambda m: m["files"][0].update(to="docs/.git/x"),
            "dot-dot source": lambda m: m["files"][0].update(**{"from": "../x.md"}),
            "unknown rewrite": lambda m: m["files"][0].update(rewrites=["persona-map"]),
            "repeated rewrite": lambda m: m["files"][0].update(rewrites=["owner_terms", "owner_terms"]),
            "drop with pattern and start": lambda m: m["drop_patterns"].append({"id": "x", "pattern": "a", "start": "b", "end": "c"}),
            "drop end without start": lambda m: m["drop_patterns"].append({"id": "x", "pattern": "a", "end": "c"}),
            "select without end": lambda m: m["files"][0].update(select={"start": "^x"}),
            "built-in flag id": lambda m: m["flag_patterns"].append({"id": "home-path", "pattern": "x"}),
            "role id with spaces": lambda m: m["persona_map"].update(EXTRA="the boss"),
            "empty files": lambda m: m.update(files=[]),
            "replace without with": lambda m: m["files"][0].update(replace=[{"find": "a"}]),
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(self.validator.errors(broken(change)), [])


class SchemaMatchesTool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load(SCHEMA)
        cls.defs = cls.schema["$defs"]

    def test_top_level_and_entry_keys(self):
        self.assertEqual(set(self.schema["properties"]), set(ex._TOP_KEYS))
        self.assertEqual(set(self.defs["entry"]["properties"]), set(ex._ENTRY_KEYS))
        self.assertEqual(set(self.schema["required"]), {"schema_version", "files"})

    def test_rewrite_names_and_order(self):
        self.assertEqual(self.defs["rewrites"]["items"]["enum"], list(ex.RULE_SET_ORDER))

    def test_builtin_flag_ids(self):
        excluded = self.defs["flagPattern"]["properties"]["id"]["allOf"][1]["not"]["enum"]
        self.assertEqual(excluded, list(ex.BUILTIN_FLAGS))

    def test_frontmatter_options(self):
        self.assertEqual(set(self.schema["properties"]["frontmatter"]["properties"]), {"drop_keys", "rename_keys", "list_separator"})

    def test_schema_version(self):
        self.assertEqual(self.schema["properties"]["schema_version"]["const"], ex.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
