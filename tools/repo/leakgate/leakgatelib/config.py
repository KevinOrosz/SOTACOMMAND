"""Load, layer, validate and compile leakgate rules files.

A rules file is JSON (``tomllib`` isn't available on Python 3.9). The schema
is documented field by field in ``tools/repo/leakgate/README.md``; this module
is its reference implementation and rejects anything it doesn't know, so a
typo fails loudly instead of silently weakening the gate.

Layering: a file may set ``"extends": "<path>"``. The base loads first, then
the overlay merges into it:

* ``regex``, ``terms``, ``paths`` and ``allow`` merge by ``id``. An overlay
  entry with a known id updates that entry's fields; a new id is appended;
  ``"disabled": true`` removes the entry.
* ``denylist_files`` and ``hashed_denylist.files`` are concatenated.
* Other settings in ``hashed_denylist``, ``env_denylist`` and ``bench`` are
  replaced key by key.

Relative paths inside a rules file resolve against that file's directory.
"""

import fnmatch
import json
import os
import re
from typing import Any, Dict, FrozenSet, Iterator, List, Mapping, Optional, Pattern, Tuple

from .errors import ConfigError
from .globs import GlobSet
from .terms import (
    HashedMatcher,
    TermSpec,
    compile_term,
    is_all_caps,
    load_hash_list,
    parse_term_lines,
    read_salt_file,
    read_term_file,
    validate_salt,
)

SCHEMA_VERSION = 1
TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(TOOL_DIR, "rules.json")

RULE_TYPES = ("regex", "term", "hashed-term", "env-denylist", "path")
MODES = ("check", "bench")
#: Rule ids that always exist, whether or not their source is configured.
RESERVED_RULE_IDS = {
    "denylist-term": "term",
    "hashed-term": "hashed-term",
    "env-denylist": "env-denylist",
}
DEFAULT_ENV_VARIABLE = "LEAKGATE_DENYLIST"
DEFAULT_SALT_ENV = "LEAKGATE_SALT"
_MAX_EXTENDS_DEPTH = 8
_ID_RE = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_ENV_NAME_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")

_TOP_KEYS = frozenset(
    ["version", "extends", "description", "regex", "terms", "denylist_files",
     "hashed_denylist", "env_denylist", "paths", "allow", "bench"]
)
_REGEX_KEYS = frozenset(["id", "description", "patterns", "ignore_case", "exceptions", "always_flag", "disabled"])
_PATTERN_KEYS = frozenset(["pattern", "ignore_case", "description"])
_TERM_GROUP_KEYS = frozenset(["id", "description", "terms", "case_sensitive", "whole_word", "disabled"])
_TERM_KEYS = frozenset(["term", "case_sensitive", "whole_word"])
_PATH_KEYS = frozenset(["id", "description", "globs", "disabled"])
_ALLOW_KEYS = frozenset(["id", "reason", "paths", "rules", "types", "match", "modes", "disabled"])
_HASHED_KEYS = frozenset(["files", "salt_file", "salt_env", "required"])
_ENV_KEYS = frozenset(["variable", "enabled"])
_FILE_REF_KEYS = frozenset(["path", "optional"])
_BENCH_KEYS = frozenset(["env"])
_SOURCE = "_source"


# --------------------------------------------------------------------------
# Compiled rules


class RegexRule(object):
    """Shape detector. ``exceptions`` are regexes that, when they fully match
    the matched text, make it a non-finding (reserved example domains, the
    AWS documentation key). ``always_flag`` lists literal values (compared
    case-insensitively) that are findings even when an exception matches."""

    type = "regex"
    __slots__ = ("id", "description", "patterns", "exceptions", "always_flag")

    def __init__(self, rule_id, description, patterns, exceptions, always_flag):
        # type: (str, str, Tuple[Pattern, ...], Tuple[Pattern, ...], FrozenSet[str]) -> None
        self.id = rule_id
        self.description = description
        self.patterns = patterns
        self.exceptions = exceptions
        self.always_flag = always_flag

    def finditer(self, text: str) -> Iterator[Tuple[int, int]]:
        for pattern in self.patterns:
            for match in pattern.finditer(text):
                start, end = match.span()
                if start == end:
                    continue
                value = match.group(0)
                if value.casefold() in self.always_flag:
                    yield start, end
                elif not any(exc.fullmatch(value) for exc in self.exceptions):
                    yield start, end


class TermRule(object):
    """Literal terms, matched on normalized text (see ``textutil``)."""

    __slots__ = ("id", "type", "description", "regexes")

    def __init__(self, rule_id, rule_type, description, regexes):
        # type: (str, str, str, Tuple[Pattern, ...]) -> None
        self.id = rule_id
        self.type = rule_type
        self.description = description
        self.regexes = regexes

    def finditer(self, text: str) -> Iterator[Tuple[int, int]]:
        for regex in self.regexes:
            for match in regex.finditer(text):
                if match.end() > match.start():
                    yield match.span()


class HashedRule(object):
    """Salted hashes of names, matched as token n-grams."""

    id = "hashed-term"
    type = "hashed-term"
    __slots__ = ("matcher",)

    def __init__(self, matcher: HashedMatcher) -> None:
        self.matcher = matcher

    def finditer(self, text: str) -> Iterator[Tuple[int, int]]:
        return self.matcher.finditer(text)


class PathRule(object):
    """Files whose path matches a deny glob are findings as a whole."""

    type = "path"
    __slots__ = ("id", "description", "globs")

    def __init__(self, rule_id: str, description: str, globs: GlobSet) -> None:
        self.id = rule_id
        self.description = description
        self.globs = globs


class AllowEntry(object):
    """A scoped exception: findings of the named rules (or rule types) in the
    matching paths, optionally only when the matched text fully matches
    ``match``, and only in the listed modes."""

    __slots__ = ("id", "reason", "paths", "rules", "types", "match", "modes")

    def __init__(self, entry_id, reason, paths, rules, types, match, modes):
        # type: (str, str, GlobSet, Tuple[str, ...], FrozenSet[str], Optional[Pattern], FrozenSet[str]) -> None
        self.id = entry_id
        self.reason = reason
        self.paths = paths
        self.rules = rules
        self.types = types
        self.match = match
        self.modes = modes

    def applies(self, relpath, rule_id, rule_type, text, mode):
        # type: (str, str, str, Optional[str], str) -> bool
        if mode not in self.modes:
            return False
        if self.paths.first_match(relpath) is None:
            return False
        if rule_type not in self.types and not any(
            fnmatch.fnmatchcase(rule_id, pattern) for pattern in self.rules
        ):
            return False
        if self.match is not None and (text is None or self.match.fullmatch(text) is None):
            return False
        return True


class Config(object):
    """A compiled, ready-to-scan rule set. Build one with :func:`load_config`."""

    def __init__(self) -> None:
        self.regex_rules = []  # type: List[RegexRule]
        self.term_rules = []  # type: List[TermRule]
        self.hashed_rule = None  # type: Optional[HashedRule]
        self.path_rules = []  # type: List[PathRule]
        self.allow = []  # type: List[AllowEntry]
        self.rule_types = dict(RESERVED_RULE_IDS)  # type: Dict[str, str]
        self.bench_env = {}  # type: Dict[str, str]
        self.env_variable = DEFAULT_ENV_VARIABLE
        self.env_term_count = 0
        self.denylist_term_count = 0
        self.hashed_count = 0
        self.warnings = []  # type: List[str]
        self.sources = []  # type: List[str]

    def content_rules(self) -> List[Any]:
        """Every rule that inspects text, in a stable order."""
        rules = list(self.regex_rules) + list(self.term_rules)  # type: List[Any]
        if self.hashed_rule is not None:
            rules.append(self.hashed_rule)
        return rules

    def allowed_by(self, relpath, rule_id, rule_type, text, mode):
        # type: (str, str, str, Optional[str], str) -> Tuple[str, ...]
        return tuple(
            entry.id for entry in self.allow if entry.applies(relpath, rule_id, rule_type, text, mode)
        )


# --------------------------------------------------------------------------
# Raw loading and shape validation


def display_path(path: str) -> str:
    """A path for messages: relative to the working directory when inside it."""
    try:
        relative = os.path.relpath(path)
    except ValueError:
        return path
    return path if relative.startswith("..") else relative.replace(os.sep, "/")


def _fail(source: str, message: str) -> ConfigError:
    return ConfigError("%s: %s" % (display_path(source), message))


def _check_keys(obj: Mapping[str, Any], allowed: FrozenSet[str], where: str, source: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise _fail(source, "%s: unknown key(s) %s (allowed: %s)" % (where, ", ".join(unknown), ", ".join(sorted(allowed))))


def _check_type(value: Any, expected: Any, where: str, source: str) -> None:
    if expected is bool:
        ok = isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, expected)
    if not ok:
        names = {str: "a string", bool: "true or false", int: "an integer", list: "a list", dict: "an object"}
        raise _fail(source, "%s must be %s" % (where, names.get(expected, str(expected))))


def _check_str_list(value: Any, where: str, source: str, allow_empty: bool = True) -> None:
    _check_type(value, list, where, source)
    if not allow_empty and not value:
        raise _fail(source, "%s must not be empty" % where)
    for index, item in enumerate(value):
        _check_type(item, str, "%s[%d]" % (where, index), source)


def _check_id(entry: Mapping[str, Any], where: str, source: str) -> str:
    if "id" not in entry:
        raise _fail(source, "%s needs an \"id\"" % where)
    _check_type(entry["id"], str, where + ".id", source)
    if not _ID_RE.match(entry["id"]):
        raise _fail(source, "%s.id %r must be lowercase letters, digits and hyphens" % (where, entry["id"]))
    return entry["id"]


def _validate_entries(data, key, allowed, source, extra):
    # type: (Dict[str, Any], str, FrozenSet[str], str, Any) -> None
    if key not in data:
        return
    _check_type(data[key], list, key, source)
    seen = set()
    for index, entry in enumerate(data[key]):
        where = "%s[%d]" % (key, index)
        _check_type(entry, dict, where, source)
        entry_id = _check_id(entry, where, source)
        where = "%s %r" % (key, entry_id)
        if entry_id in seen:
            raise _fail(source, "%s is defined twice in this file" % where)
        seen.add(entry_id)
        _check_keys(entry, allowed, where, source)
        if "disabled" in entry:
            _check_type(entry["disabled"], bool, where + ".disabled", source)
        if "description" in entry:
            _check_type(entry["description"], str, where + ".description", source)
        extra(entry, where, source)


def _shape_regex(entry, where, source):
    if "patterns" in entry:
        _check_type(entry["patterns"], list, where + ".patterns", source)
        for index, item in enumerate(entry["patterns"]):
            item_where = "%s.patterns[%d]" % (where, index)
            if isinstance(item, dict):
                _check_keys(item, _PATTERN_KEYS, item_where, source)
                if "pattern" not in item:
                    raise _fail(source, "%s needs a \"pattern\"" % item_where)
                _check_type(item["pattern"], str, item_where + ".pattern", source)
                if "ignore_case" in item:
                    _check_type(item["ignore_case"], bool, item_where + ".ignore_case", source)
            else:
                _check_type(item, str, item_where, source)
    if "ignore_case" in entry:
        _check_type(entry["ignore_case"], bool, where + ".ignore_case", source)
    for key in ("exceptions", "always_flag"):
        if key in entry:
            _check_str_list(entry[key], where + "." + key, source)


def _shape_terms(entry, where, source):
    if "terms" in entry:
        _check_type(entry["terms"], list, where + ".terms", source)
        for index, item in enumerate(entry["terms"]):
            item_where = "%s.terms[%d]" % (where, index)
            if isinstance(item, dict):
                _check_keys(item, _TERM_KEYS, item_where, source)
                if "term" not in item:
                    raise _fail(source, "%s needs a \"term\"" % item_where)
                _check_type(item["term"], str, item_where + ".term", source)
                for flag in ("case_sensitive", "whole_word"):
                    if flag in item:
                        _check_type(item[flag], bool, item_where + "." + flag, source)
            else:
                _check_type(item, str, item_where, source)
    for flag in ("case_sensitive", "whole_word"):
        if flag in entry and entry[flag] is not None:
            _check_type(entry[flag], bool, where + "." + flag, source)


def _shape_paths(entry, where, source):
    if "globs" in entry:
        _check_str_list(entry["globs"], where + ".globs", source)


def _shape_allow(entry, where, source):
    for key in ("paths", "rules", "types", "modes"):
        if key in entry:
            _check_str_list(entry[key], where + "." + key, source)
    for key in ("reason", "match"):
        if key in entry:
            _check_type(entry[key], str, where + "." + key, source)


def _validate_file_refs(value: Any, where: str, source: str) -> None:
    _check_type(value, list, where, source)
    for index, item in enumerate(value):
        item_where = "%s[%d]" % (where, index)
        if isinstance(item, dict):
            _check_keys(item, _FILE_REF_KEYS, item_where, source)
            if "path" not in item:
                raise _fail(source, "%s needs a \"path\"" % item_where)
            _check_type(item["path"], str, item_where + ".path", source)
            if "optional" in item:
                _check_type(item["optional"], bool, item_where + ".optional", source)
        else:
            _check_type(item, str, item_where, source)


def _validate_shape(data: Any, source: str) -> None:
    _check_type(data, dict, "the top level", source)
    _check_keys(data, _TOP_KEYS, "the top level", source)
    if data.get("version") != SCHEMA_VERSION:
        raise _fail(source, "\"version\" must be %d" % SCHEMA_VERSION)
    for key in ("extends", "description"):
        if key in data:
            _check_type(data[key], str, key, source)
    _validate_entries(data, "regex", _REGEX_KEYS, source, _shape_regex)
    _validate_entries(data, "terms", _TERM_GROUP_KEYS, source, _shape_terms)
    _validate_entries(data, "paths", _PATH_KEYS, source, _shape_paths)
    _validate_entries(data, "allow", _ALLOW_KEYS, source, _shape_allow)
    if "denylist_files" in data:
        _validate_file_refs(data["denylist_files"], "denylist_files", source)
    if "hashed_denylist" in data:
        hashed = data["hashed_denylist"]
        _check_type(hashed, dict, "hashed_denylist", source)
        _check_keys(hashed, _HASHED_KEYS, "hashed_denylist", source)
        if "files" in hashed:
            _validate_file_refs(hashed["files"], "hashed_denylist.files", source)
        for key in ("salt_file", "salt_env"):
            if key in hashed and hashed[key] is not None:
                _check_type(hashed[key], str, "hashed_denylist." + key, source)
        if "required" in hashed:
            _check_type(hashed["required"], bool, "hashed_denylist.required", source)
    if "env_denylist" in data:
        env = data["env_denylist"]
        _check_type(env, dict, "env_denylist", source)
        _check_keys(env, _ENV_KEYS, "env_denylist", source)
        if "variable" in env:
            _check_type(env["variable"], str, "env_denylist.variable", source)
            if not _ENV_NAME_RE.match(env["variable"]):
                raise _fail(source, "env_denylist.variable %r is not a valid variable name" % env["variable"])
        if "enabled" in env:
            _check_type(env["enabled"], bool, "env_denylist.enabled", source)
    if "bench" in data:
        bench = data["bench"]
        _check_type(bench, dict, "bench", source)
        _check_keys(bench, _BENCH_KEYS, "bench", source)
        if "env" in bench:
            _check_type(bench["env"], dict, "bench.env", source)
            for name, value in bench["env"].items():
                _check_type(value, str, "bench.env.%s" % name, source)


def _resolve(base_dir: str, path: str) -> str:
    return os.path.normpath(os.path.join(base_dir, os.path.expanduser(path)))


def _file_refs(value: List[Any], base_dir: str, source: str) -> List[Dict[str, Any]]:
    refs = []
    for item in value:
        if isinstance(item, str):
            item = {"path": item}
        refs.append({"path": _resolve(base_dir, item["path"]), "optional": bool(item.get("optional", False)), _SOURCE: source})
    return refs


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ConfigError("%s: rules file not found" % display_path(path))
    except OSError as exc:
        raise ConfigError("%s: can't read rules file: %s" % (display_path(path), exc.strerror or exc))
    except ValueError as exc:
        raise ConfigError("%s: not valid JSON: %s" % (display_path(path), exc))


def _empty_raw() -> Dict[str, Any]:
    return {
        "regex": [], "terms": [], "paths": [], "allow": [],
        "denylist_files": [],
        "hashed_denylist": {"files": []},
        "env_denylist": {},
        "bench": {},
    }


def _merge_by_id(base: List[Dict[str, Any]], overlay: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = [dict(entry) for entry in base]
    index = {entry["id"]: position for position, entry in enumerate(merged)}
    for entry in overlay:
        if entry["id"] in index:
            position = index[entry["id"]]
            merged[position] = dict(merged[position], **entry)
        else:
            index[entry["id"]] = len(merged)
            merged.append(dict(entry))
    return [entry for entry in merged if not entry.get("disabled", False)]


def _load_layered(path: str, depth: int, chain: Tuple[str, ...]) -> Tuple[Dict[str, Any], List[str]]:
    path = os.path.abspath(path)
    if path in chain:
        raise ConfigError("%s: \"extends\" forms a cycle" % display_path(path))
    if depth > _MAX_EXTENDS_DEPTH:
        raise ConfigError("%s: \"extends\" is nested more than %d deep" % (display_path(path), _MAX_EXTENDS_DEPTH))
    data = _read_json(path)
    _validate_shape(data, path)
    base_dir = os.path.dirname(path)

    if "extends" in data:
        merged, sources = _load_layered(_resolve(base_dir, data["extends"]), depth + 1, chain + (path,))
    else:
        merged, sources = _empty_raw(), []
    sources = sources + [path]

    for key in ("regex", "terms", "paths", "allow"):
        overlay = [dict(entry, **{_SOURCE: path}) for entry in data.get(key, [])]
        merged[key] = _merge_by_id(merged[key], overlay)
    merged["denylist_files"] = merged["denylist_files"] + _file_refs(data.get("denylist_files", []), base_dir, path)

    hashed_overlay = data.get("hashed_denylist", {})
    hashed = dict(merged["hashed_denylist"])
    hashed["files"] = hashed.get("files", []) + _file_refs(hashed_overlay.get("files", []), base_dir, path)
    if hashed_overlay.get("salt_file") is not None:
        hashed["salt_file"] = _resolve(base_dir, hashed_overlay["salt_file"])
    for key in ("salt_env", "required"):
        if key in hashed_overlay:
            hashed[key] = hashed_overlay[key]
    merged["hashed_denylist"] = hashed
    merged["env_denylist"] = dict(merged["env_denylist"], **data.get("env_denylist", {}))
    merged["bench"] = dict(merged["bench"], **data.get("bench", {}))
    if "description" in data:
        merged["description"] = data["description"]
    return merged, sources


# --------------------------------------------------------------------------
# Compilation


def _compile_regex(text: str, flags: int, where: str, source: str) -> Pattern:
    try:
        return re.compile(text, flags)
    except re.error as exc:
        raise _fail(source, "%s does not compile: %s" % (where, exc))


def _compile_regex_rules(entries: List[Dict[str, Any]]) -> List[RegexRule]:
    rules = []
    for entry in entries:
        source = entry[_SOURCE]
        where = "regex %r" % entry["id"]
        patterns_raw = entry.get("patterns")
        if not patterns_raw:
            raise _fail(source, "%s needs a non-empty \"patterns\" list" % where)
        default_ignore = bool(entry.get("ignore_case", False))
        patterns = []
        for index, item in enumerate(patterns_raw):
            if isinstance(item, dict):
                text = item["pattern"]
                ignore = bool(item.get("ignore_case", default_ignore))
            else:
                text, ignore = item, default_ignore
            pattern = _compile_regex(text, re.IGNORECASE if ignore else 0, "%s.patterns[%d]" % (where, index), source)
            if pattern.match(""):
                raise _fail(source, "%s.patterns[%d] matches the empty string" % (where, index))
            patterns.append(pattern)
        exceptions = tuple(
            _compile_regex(text, 0, "%s.exceptions[%d]" % (where, index), source)
            for index, text in enumerate(entry.get("exceptions", []))
        )
        always = frozenset(value.casefold() for value in entry.get("always_flag", []))
        rules.append(RegexRule(entry["id"], entry.get("description", ""), tuple(patterns), exceptions, always))
    return rules


def _term_spec(item: Any, group: Dict[str, Any], origin: str) -> TermSpec:
    if isinstance(item, str):
        term, case_sensitive, whole_word = item, None, None
    else:
        term = item["term"]
        case_sensitive = item.get("case_sensitive")
        whole_word = item.get("whole_word")
    if case_sensitive is None:
        case_sensitive = group.get("case_sensitive")
    if whole_word is None:
        whole_word = group.get("whole_word")
    if case_sensitive is None:
        case_sensitive = is_all_caps(term)
    if whole_word is None:
        whole_word = True
    return TermSpec(term, bool(case_sensitive), bool(whole_word), origin)


def _compile_specs(specs: List[TermSpec]) -> Tuple[Pattern, ...]:
    return tuple(compile_term(spec) for spec in specs)


def _compile_term_groups(entries: List[Dict[str, Any]]) -> List[TermRule]:
    rules = []
    for entry in entries:
        source = entry[_SOURCE]
        where = "terms %r" % entry["id"]
        items = entry.get("terms", [])
        specs = [
            _term_spec(item, entry, "%s: %s.terms[%d]" % (display_path(source), where, index))
            for index, item in enumerate(items)
        ]
        for spec in specs:
            if not spec.term.strip():
                raise ConfigError("%s is empty" % spec.origin)
        rules.append(TermRule(entry["id"], "term", entry.get("description", ""), _compile_specs(specs)))
    return rules


def _read_denylists(refs: List[Dict[str, Any]], config: Config) -> List[TermSpec]:
    specs = []
    for ref in refs:
        path = ref["path"]
        if not os.path.exists(path):
            if ref["optional"]:
                continue
            raise ConfigError("%s: denylist not found (referenced by %s)" % (display_path(path), display_path(ref[_SOURCE])))
        specs.extend(read_term_file(path, display_path(path)))
    return specs


def _compile_hashed(raw: Dict[str, Any], env: Mapping[str, str], explicit_salt: Optional[bytes], config: Config) -> None:
    refs = raw.get("files", [])
    digests = set()  # type: set
    loaded = 0
    required = bool(raw.get("required", True))
    for ref in refs:
        path = ref["path"]
        if not os.path.exists(path):
            if ref["optional"]:
                continue
            raise ConfigError("%s: hashed denylist not found (referenced by %s)" % (display_path(path), display_path(ref[_SOURCE])))
        digests.update(load_hash_list(path, display_path(path)))
        loaded += 1
        if ref.get("explicit"):
            required = True
    if not loaded:
        return
    # Salt precedence: --salt-file, then the config's salt_file when it exists
    # (an owner's local copy), then the salt variable (a CI secret).
    variable = raw.get("salt_env") or DEFAULT_SALT_ENV
    salt = explicit_salt
    salt_path = raw.get("salt_file")
    if salt is None and salt_path and os.path.exists(salt_path):
        salt = read_salt_file(salt_path, display_path(salt_path))
    if salt is None:
        value = env.get(variable, "")
        if value.strip():
            salt = validate_salt(value, "$" + variable)
    if salt is None:
        where = "pass --salt-file or set %s" % variable
        if salt_path:
            where = "%s not found; %s" % (display_path(salt_path), where)
        message = "a hashed denylist is configured but no salt is available (%s)" % where
        if required:
            raise ConfigError(message)
        config.warnings.append(message + "; hashed-term matching is OFF for this run")
        return
    config.hashed_rule = HashedRule(HashedMatcher(frozenset(digests), salt))
    config.hashed_count = len(digests)


def _compile_paths(entries: List[Dict[str, Any]]) -> List[PathRule]:
    rules = []
    for entry in entries:
        source = entry[_SOURCE]
        where = "paths %r" % entry["id"]
        globs = entry.get("globs")
        if not globs:
            raise _fail(source, "%s needs a non-empty \"globs\" list" % where)
        try:
            compiled = GlobSet(globs)
        except ValueError as exc:
            raise _fail(source, "%s: %s" % (where, exc))
        rules.append(PathRule(entry["id"], entry.get("description", ""), compiled))
    return rules


def _compile_allow(entries: List[Dict[str, Any]], rule_types: Dict[str, str]) -> List[AllowEntry]:
    allow = []
    for entry in entries:
        source = entry[_SOURCE]
        where = "allow %r" % entry["id"]
        if not entry.get("reason", "").strip():
            raise _fail(source, "%s needs a \"reason\": say why this exception is safe" % where)
        if not entry.get("paths"):
            raise _fail(source, "%s needs a non-empty \"paths\" list" % where)
        rules = tuple(entry.get("rules", []))
        types = frozenset(entry.get("types", []))
        if not rules and not types:
            raise _fail(source, "%s needs \"rules\" or \"types\"" % where)
        for pattern in rules:
            if any(ch in pattern for ch in "*?["):
                if not any(fnmatch.fnmatchcase(rule_id, pattern) for rule_id in rule_types):
                    raise _fail(source, "%s: rule pattern %r matches no rule" % (where, pattern))
            elif pattern not in rule_types:
                raise _fail(source, "%s: unknown rule id %r (known: %s)" % (where, pattern, ", ".join(sorted(rule_types))))
        for rule_type in types:
            if rule_type not in RULE_TYPES:
                raise _fail(source, "%s: unknown rule type %r (known: %s)" % (where, rule_type, ", ".join(RULE_TYPES)))
        modes = frozenset(entry.get("modes", MODES))
        for mode in modes:
            if mode not in MODES:
                raise _fail(source, "%s: unknown mode %r (known: %s)" % (where, mode, ", ".join(MODES)))
        if not modes:
            raise _fail(source, "%s: \"modes\" must not be empty" % where)
        match = None
        if "match" in entry:
            match = _compile_regex(entry["match"], 0, where + ".match", source)
        try:
            paths = GlobSet(entry["paths"])
        except ValueError as exc:
            raise _fail(source, "%s: %s" % (where, exc))
        allow.append(AllowEntry(entry["id"], entry["reason"], paths, rules, types, match, modes))
    return allow


def _register_ids(config: Config, entries: List[Dict[str, Any]], rule_type: str) -> None:
    for entry in entries:
        rule_id = entry["id"]
        if rule_id in config.rule_types:
            reserved = " (reserved)" if rule_id in RESERVED_RULE_IDS else ""
            raise _fail(entry[_SOURCE], "rule id %r is already used%s" % (rule_id, reserved))
        config.rule_types[rule_id] = rule_type


def _compile(raw: Dict[str, Any], env: Mapping[str, str], explicit_salt: Optional[bytes], sources: List[str]) -> Config:
    config = Config()
    config.sources = [display_path(source) for source in sources]
    _register_ids(config, raw["regex"], "regex")
    _register_ids(config, raw["terms"], "term")
    _register_ids(config, raw["paths"], "path")

    config.regex_rules = _compile_regex_rules(raw["regex"])
    config.term_rules = _compile_term_groups(raw["terms"])

    denylist_specs = _read_denylists(raw["denylist_files"], config)
    config.denylist_term_count = len(denylist_specs)
    if denylist_specs:
        config.term_rules.append(
            TermRule("denylist-term", "term", "terms from plaintext denylist files", _compile_specs(denylist_specs))
        )

    env_settings = raw["env_denylist"]
    config.env_variable = env_settings.get("variable", DEFAULT_ENV_VARIABLE)
    if env_settings.get("enabled", True):
        value = env.get(config.env_variable, "")
        if value.strip():
            env_specs = parse_term_lines(value, "$" + config.env_variable)
            config.env_term_count = len(env_specs)
            if env_specs:
                config.term_rules.append(
                    TermRule("env-denylist", "env-denylist", "terms from $" + config.env_variable, _compile_specs(env_specs))
                )

    _compile_hashed(raw["hashed_denylist"], env, explicit_salt, config)
    config.path_rules = _compile_paths(raw["paths"])
    config.allow = _compile_allow(raw["allow"], config.rule_types)
    config.bench_env = dict(raw["bench"].get("env", {}))
    return config


def load_config(
    path: Optional[str] = None,
    denylist: Tuple[str, ...] = (),
    hashed_denylist: Tuple[str, ...] = (),
    salt_file: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    bench: bool = False,
) -> Config:
    """Load and compile a rules file.

    ``path`` defaults to ``rules.json`` next to ``leakgate.py``. ``denylist``
    and ``hashed_denylist`` add plaintext and hashed lists (as the CLI flags
    do); lists given here are always required to load. ``salt_file`` wins
    over the config's ``salt_file`` and the ``LEAKGATE_SALT`` variable.
    ``env`` defaults to ``os.environ``. With ``bench=True`` the environment is
    replaced by the file's ``bench.env`` so the benchmark is hermetic.
    """
    raw, sources = _load_layered(path or DEFAULT_CONFIG_PATH, 0, ())
    for item in denylist:
        raw["denylist_files"].append({"path": os.path.abspath(item), "optional": False, _SOURCE: "--denylist"})
    for item in hashed_denylist:
        raw["hashed_denylist"]["files"].append(
            {"path": os.path.abspath(item), "optional": False, "explicit": True, _SOURCE: "--hashed-denylist"}
        )
    explicit_salt = read_salt_file(salt_file, display_path(salt_file)) if salt_file else None
    if bench:
        env = dict(raw["bench"].get("env", {}))
    elif env is None:
        env = os.environ
    return _compile(raw, env, explicit_salt, sources)
