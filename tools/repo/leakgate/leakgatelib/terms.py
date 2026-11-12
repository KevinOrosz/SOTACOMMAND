"""Name lists: plaintext denylists, literal term matching and hashed terms.

Three rule types match names rather than shapes:

``term``
    Literal terms from ``rules.json`` groups or from a plaintext ``--denylist``
    file (rule id ``denylist-term``).
``env-denylist``
    The same line format, read from an environment variable (normally a CI
    secret) and never written anywhere.
``hashed-term``
    Salted SHA-256 hashes of normalized token n-grams (n <= 4), so a public
    repository can block names without printing them.

Plaintext list format, one term per line::

    # a comment (only whole-line comments; '#' inside a term is literal)
    Avery Example               whole word, case-insensitive
    PROJECTNAME                 no lowercase letters, so case-sensitive
    [case-insensitive] ACME     force case-insensitive
    [substring] acmecorp        also match inside longer words
    [case-sensitive, whole-word] Acme

Defaults: whole word, and case-sensitive exactly when the term has at least
two letters and none of them is lowercase. That keeps an all-caps codename
from matching the same word in ordinary prose.
"""

import hashlib
import json
import os
import re
import secrets
from typing import Dict, FrozenSet, Iterable, Iterator, List, Optional, Pattern, Tuple

from .errors import ConfigError
from .textutil import TOKEN_RE, normalize_text

#: Longest name, in tokens, that hashed matching can recognise.
MAX_NGRAM = 4
#: Shortest salt accepted, in characters.
MIN_SALT_CHARS = 16
#: Domain-separation tag mixed into every hash. Changing the hashing scheme
#: means bumping this, which invalidates every published list on purpose.
HASH_SCHEME = b"leakgate-hashed-term-v1"

FLAG_WORDS = {
    "case-sensitive": ("case_sensitive", True),
    "case-insensitive": ("case_sensitive", False),
    "whole-word": ("whole_word", True),
    "substring": ("whole_word", False),
}

_FLAGS_RE = re.compile(r"\A\[([^\]]*)\]\s*(.*)\Z")
_SEPARATOR_SPLIT_RE = re.compile(r"([\s._-]+)")
#: Between the words of a multi-word term: any run of whitespace, dots,
#: hyphens or underscores, or nothing at all. So "Avery Example" also
#: matches "avery-example", "avery_example" and "AveryExample".
FLEXIBLE_SEPARATOR = r"[\s._-]*"
_NOT_AFTER_WORD = r"(?<![^\W_])"
_NOT_BEFORE_WORD = r"(?![^\W_])"
_HEX_DIGEST_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class TermSpec(object):
    """One term and how to match it. ``origin`` locates it for error messages
    ("denylist.txt:12") without repeating the term itself."""

    __slots__ = ("term", "case_sensitive", "whole_word", "origin")

    def __init__(self, term: str, case_sensitive: bool, whole_word: bool, origin: str) -> None:
        self.term = term
        self.case_sensitive = case_sensitive
        self.whole_word = whole_word
        self.origin = origin

    def __repr__(self) -> str:  # never print the term itself
        return "TermSpec(origin=%r, case_sensitive=%r, whole_word=%r)" % (
            self.origin,
            self.case_sensitive,
            self.whole_word,
        )


def is_all_caps(term: str) -> bool:
    """True when ``term`` has two or more letters and none is lowercase."""
    letters = [ch for ch in term if ch.isalpha()]
    return (
        len(letters) >= 2
        and not any(ch.islower() for ch in letters)
        and any(ch.isupper() for ch in letters)
    )


def parse_term_lines(text: str, source: str) -> List[TermSpec]:
    """Parse the plaintext list format described in the module docstring."""
    specs = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        case_sensitive = None  # type: Optional[bool]
        whole_word = None  # type: Optional[bool]
        match = _FLAGS_RE.match(line)
        if match:
            words = [w.strip().lower() for w in match.group(1).split(",")]
            if words and all(w in FLAG_WORDS for w in words):
                for word in words:
                    attribute, value = FLAG_WORDS[word]
                    if attribute == "case_sensitive":
                        case_sensitive = value
                    else:
                        whole_word = value
                line = match.group(2).strip()
                if not line:
                    raise ConfigError("%s:%d: flags without a term" % (source, lineno))
        if case_sensitive is None:
            case_sensitive = is_all_caps(line)
        if whole_word is None:
            whole_word = True
        specs.append(TermSpec(line, case_sensitive, whole_word, "%s:%d" % (source, lineno)))
    return specs


def read_term_file(path: str, display: str) -> List[TermSpec]:
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            text = handle.read()
    except OSError as exc:
        raise ConfigError("%s: can't read denylist: %s" % (display, exc.strerror or exc))
    except UnicodeDecodeError:
        raise ConfigError("%s: denylist is not valid UTF-8" % display)
    return parse_term_lines(text, display)


def compile_term(spec: TermSpec) -> Pattern:
    """Compile a literal term into a regex over normalized text."""
    term = normalize_text(spec.term).strip()
    if not term:
        raise ConfigError("%s: term is empty after normalization" % spec.origin)
    parts = _SEPARATOR_SPLIT_RE.split(term)
    pieces = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            pieces.append(re.escape(part))
            continue
        leading = index == 1 and parts[0] == ""
        trailing = index == len(parts) - 2 and parts[-1] == ""
        pieces.append(re.escape(part) if (leading or trailing) else FLEXIBLE_SEPARATOR)
    body = "".join(pieces)
    if spec.whole_word and term[0].isalnum():
        body = _NOT_AFTER_WORD + body
    if spec.whole_word and term[-1].isalnum():
        body = body + _NOT_BEFORE_WORD
    try:
        return re.compile(body, 0 if spec.case_sensitive else re.IGNORECASE)
    except re.error as exc:  # pragma: no cover - escaped input always compiles
        raise ConfigError("%s: term does not compile: %s" % (spec.origin, exc))


# --------------------------------------------------------------------------
# Hashing


def ngram_forms(text: str) -> Tuple[List[str], str, str]:
    """Tokens of ``text`` and its case-insensitive and case-sensitive keys."""
    tokens = TOKEN_RE.findall(normalize_text(text))
    insensitive = " ".join(token.casefold() for token in tokens)
    sensitive = " ".join(tokens)
    return tokens, insensitive, sensitive


class Hasher(object):
    """Salted SHA-256 with domain separation.

    ``digest = sha256(len(salt) || salt || 0x00 || scheme || 0x00 || domain || 0x00 || key)``
    where ``domain`` is ``ci`` (case-insensitive key: tokens casefolded) or
    ``cs`` (case-sensitive key), and ``key`` is the normalized tokens joined
    by single spaces, UTF-8 encoded. The salted prefix is hashed once and
    copied per key, which keeps scanning fast.
    """

    __slots__ = ("_ci", "_cs")

    def __init__(self, salt: bytes) -> None:
        base = hashlib.sha256()
        base.update(len(salt).to_bytes(4, "big"))
        base.update(salt)
        base.update(b"\x00" + HASH_SCHEME + b"\x00")
        self._ci = base.copy()
        self._ci.update(b"ci\x00")
        self._cs = base.copy()
        self._cs.update(b"cs\x00")

    def insensitive(self, key: str) -> str:
        digest = self._ci.copy()
        digest.update(key.encode("utf-8"))
        return digest.hexdigest()

    def sensitive(self, key: str) -> str:
        digest = self._cs.copy()
        digest.update(key.encode("utf-8"))
        return digest.hexdigest()


def hash_specs(specs: Iterable[TermSpec], salt: bytes) -> List[str]:
    """Hash terms for publication. Returns sorted, de-duplicated hex digests.

    A multi-token term is also stored with its tokens run together, so
    "Avery Example" catches "AveryExample" as well. A case-sensitive term
    without case variation (all lowercase, digits, scripts without case) is
    stored case-insensitively: the result is the same match for every input
    the term could be written as.
    """
    hasher = Hasher(salt)
    problems = []
    digests = set()
    for spec in specs:
        if not spec.whole_word:
            problems.append("%s: [substring] terms can't be hashed (hashing matches whole tokens)" % spec.origin)
            continue
        tokens, insensitive, sensitive = ngram_forms(spec.term)
        if not tokens:
            problems.append("%s: term has no letters or digits to hash" % spec.origin)
            continue
        if len(tokens) > MAX_NGRAM:
            problems.append(
                "%s: term has %d tokens; hashed matching supports at most %d"
                % (spec.origin, len(tokens), MAX_NGRAM)
            )
            continue
        keys = [(insensitive, sensitive)]
        if len(tokens) > 1:
            keys.append((insensitive.replace(" ", ""), sensitive.replace(" ", "")))
        for key_ci, key_cs in keys:
            if spec.case_sensitive and key_cs != key_ci:
                digests.add(hasher.sensitive(key_cs))
            else:
                digests.add(hasher.insensitive(key_ci))
    if problems:
        raise ConfigError("can't hash the denylist:\n  " + "\n  ".join(problems))
    return sorted(digests)


def load_hash_list(path: str, display: str) -> FrozenSet[str]:
    """Load a published hashed denylist: a JSON list of 64-hex-digit strings."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise ConfigError("%s: can't read hashed denylist: %s" % (display, exc.strerror or exc))
    except ValueError as exc:
        raise ConfigError("%s: hashed denylist is not valid JSON: %s" % (display, exc))
    if not isinstance(data, list):
        raise ConfigError("%s: hashed denylist must be a JSON list of hex digests" % display)
    digests = set()
    for index, item in enumerate(data):
        if not isinstance(item, str) or not _HEX_DIGEST_RE.match(item.lower()):
            raise ConfigError(
                "%s: item %d is not a 64-character SHA-256 hex digest" % (display, index)
            )
        digests.add(item.lower())
    return frozenset(digests)


# --------------------------------------------------------------------------
# Salt


def validate_salt(text: str, display: str) -> bytes:
    salt = text.strip()
    if len(salt) < MIN_SALT_CHARS:
        raise ConfigError(
            "%s: salt must be at least %d characters (got %d)" % (display, MIN_SALT_CHARS, len(salt))
        )
    return salt.encode("utf-8")


def read_salt_file(path: str, display: Optional[str] = None) -> bytes:
    display = display or path
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return validate_salt(handle.read(), display)
    except OSError as exc:
        raise ConfigError("%s: can't read salt file: %s" % (display, exc.strerror or exc))
    except UnicodeDecodeError:
        raise ConfigError("%s: salt file is not valid UTF-8" % display)


def create_salt_file(path: str) -> None:
    """Create ``path`` holding 64 random hex characters, readable by the owner
    only. Refuses to overwrite an existing file."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise ConfigError("%s: already exists; not overwriting a salt" % path)
    except OSError as exc:
        raise ConfigError("%s: can't create salt file: %s" % (path, exc.strerror or exc))
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(secrets.token_hex(32) + "\n")


# --------------------------------------------------------------------------
# Matching hashed terms in text


class HashedMatcher(object):
    """Find spans of normalized text whose token n-grams are in a hash set.

    N-grams run across line breaks, so a name wrapped onto two lines still
    matches. Hash results are cached across files: most n-grams in a
    repository repeat.
    """

    _CACHE_LIMIT = 400000

    def __init__(self, digests: FrozenSet[str], salt: bytes, max_ngram: int = MAX_NGRAM) -> None:
        self._digests = digests
        self._hasher = Hasher(salt)
        self._max_ngram = max_ngram
        self._cache_ci = {}  # type: Dict[str, bool]
        self._cache_cs = {}  # type: Dict[str, bool]

    def __len__(self) -> int:
        return len(self._digests)

    def _hit(self, key_ci: str, key_cs: str) -> bool:
        cache = self._cache_ci
        found = cache.get(key_ci)
        if found is None:
            if len(cache) > self._CACHE_LIMIT:
                cache.clear()
            found = self._hasher.insensitive(key_ci) in self._digests
            cache[key_ci] = found
        if found:
            return True
        if key_cs == key_ci:
            return False
        cache = self._cache_cs
        found = cache.get(key_cs)
        if found is None:
            if len(cache) > self._CACHE_LIMIT:
                cache.clear()
            found = self._hasher.sensitive(key_cs) in self._digests
            cache[key_cs] = found
        return found

    def finditer(self, text: str) -> Iterator[Tuple[int, int]]:
        """Yield ``(start, end)`` spans in ``text`` (already normalized)."""
        if not self._digests:
            return
        matches = list(TOKEN_RE.finditer(text))
        sensitive = [m.group(0) for m in matches]
        insensitive = [token.casefold() for token in sensitive]
        count = len(matches)
        for i in range(count):
            key_ci = insensitive[i]
            key_cs = sensitive[i]
            for n in range(1, self._max_ngram + 1):
                j = i + n - 1
                if j >= count:
                    break
                if n > 1:
                    key_ci = key_ci + " " + insensitive[j]
                    key_cs = key_cs + " " + sensitive[j]
                if self._hit(key_ci, key_cs):
                    yield matches[i].start(), matches[j].end()
