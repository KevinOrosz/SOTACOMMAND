"""This lane's shipped files hold no private markers.

CI runs the leak gate over everything outside fixtures/ and internal/, so the
tool, its tests and the schema must not contain the markers they handle. This
check doesn't depend on the leak gate: it builds its patterns at runtime from
the example manifest and the tool's own home-path pattern.

The example manifests are excluded on purpose: a persona map has to name the
personas it maps. Keep the real manifest out of the repository.
"""

import json
import re
import unittest

from .support import EXAMPLE_MANIFEST, REPO_ROOT, ex

LANE_FILES = sorted(
    list((REPO_ROOT / "tools" / "repo" / "extract").rglob("*"))
    + list((REPO_ROOT / "tests" / "extract").rglob("*"))
    + [REPO_ROOT / "export" / "manifest.schema.json", REPO_ROOT / "export" / ".gitignore"]
)
SAFE_EMAIL_DOMAINS = ("example.com", "example.org", "example.invalid")


def lane_texts():
    for path in LANE_FILES:
        if path.is_file() and "__pycache__" not in path.parts:
            yield path.relative_to(REPO_ROOT).as_posix(), path.read_text(encoding="utf-8")


class LaneHygiene(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(str(EXAMPLE_MANIFEST), encoding="utf-8") as handle:
            manifest = json.load(handle)
        flags = {item["id"]: item["pattern"] for item in manifest["flag_patterns"]}
        cls.patterns = {
            "persona name": re.compile(r"(?<![^\W_])(?:%s)(?![^\W_])" % "|".join(manifest["persona_map"])),
            "home path": ex.HOME_PATH_RE,
            "house rule code": re.compile(flags["rule-code"]),
            "dated ruling": re.compile(flags["dated-ruling"]),
            "model name": re.compile(flags["vendor-model"]),
            "client name": re.compile(flags["client-name"]),
        }
        cls.email = re.compile(flags["email-address"])

    def test_no_private_markers_in_lane_files(self):
        texts = dict(lane_texts())
        self.assertIn("tools/repo/extract/extract.py", texts)
        for path, text in texts.items():
            for label, regex in self.patterns.items():
                with self.subTest(path=path, marker=label):
                    match = regex.search(text)
                    self.assertIsNone(match, "%s holds a %s: %r" % (path, label, match and match.group(0)))
            for match in self.email.finditer(text):
                self.assertTrue(match.group(0).lower().endswith(SAFE_EMAIL_DOMAINS), "%s holds %r" % (path, match.group(0)))

    def test_the_tool_and_schema_name_no_owner(self):
        for path, text in lane_texts():
            if path.startswith(("tools/", "export/")):
                self.assertNotIn("Avery", text, path)


if __name__ == "__main__":
    unittest.main()
