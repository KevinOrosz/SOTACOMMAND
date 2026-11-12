"""Small, deterministic text helpers shared by the renderers."""

import json
import os
import re
from typing import Any, Dict, Iterable

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")

# Plain YAML scalars we emit unquoted: no colon, hash, quote or leading indicator.
_SAFE_PLAIN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ ,.()/*+=-]*$")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}
_PLACEHOLDER = re.compile(r"\{\{[A-Z_]+\}\}")


def yaml_scalar(value: str) -> str:
    """Return ``value`` as a YAML scalar, double-quoted unless trivially safe."""
    if (
        _SAFE_PLAIN.match(value)
        and value == value.strip()
        and value.lower() not in _YAML_WORDS
        and not value[0].isdigit()
    ):
        return value
    # A JSON string is a valid YAML double-quoted scalar.
    return json.dumps(value, ensure_ascii=False)


def json_text(data: Any) -> str:
    """Stable JSON: two-space indent, insertion order, trailing newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def template_path(*parts: str) -> str:
    return os.path.join(TEMPLATES_DIR, *parts)


def read_template(*parts: str) -> str:
    with open(template_path(*parts), "r", encoding="utf-8") as handle:
        return handle.read()


def render(template: str, values: Dict[str, str]) -> str:
    """Replace ``{{NAME}}`` placeholders. Unknown or leftover placeholders fail."""
    out = template
    for key, value in values.items():
        token = "{{%s}}" % key
        if token not in out:
            raise ValueError("template has no placeholder %s" % token)
        out = out.replace(token, value)
    leftover = _PLACEHOLDER.findall(out)
    if leftover:
        raise ValueError("unfilled template placeholder(s): %s" % ", ".join(sorted(set(leftover))))
    return out


def markdown_cell(text: str) -> str:
    """Make ``text`` safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def code_list(items: Iterable[str]) -> str:
    return ", ".join("`%s`" % item for item in items)
