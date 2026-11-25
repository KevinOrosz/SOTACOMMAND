"""Check every tracked public file, allowing only exact, reviewed synthetic fixture blobs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-staging", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    paths = args.paths or [p.decode("utf-8") for p in subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root).split(b"\x00") if p]
    allowed = json.loads(Path(__file__).with_name("fixture-digests.json").read_text())
    selected = []
    for path in paths:
        if path == "internal" or path.startswith("internal/"):
            if args.private_staging:
                continue
            parser.error("private staging files may not be tracked in a public repository")
        if path.startswith("fixtures/") and path in allowed:
            source = root / path
            if not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest() == allowed[path]:
                continue
        selected.append(path)
    if not selected:
        return 0
    return subprocess.call([sys.executable, str(root / "tools/repo/leakgate/leakgate.py"),
                            "check", "--root", str(root), "--", *selected], cwd=root)


if __name__ == "__main__":
    sys.exit(main())
