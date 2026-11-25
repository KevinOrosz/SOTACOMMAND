"""Exercise the real Linux Docker boundary using a locally created image and benign inputs."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "engine" / "tools"))
import close_review


def main():
    with tempfile.TemporaryDirectory() as build, tempfile.TemporaryDirectory() as owner, tempfile.TemporaryDirectory() as project:
        build, project = Path(build), Path(project)
        (build / "probe.c").write_text("""
#include <stdio.h>
#include <unistd.h>
int main(void) {
    FILE *f = fopen("/workspace/input.txt","r");
    if (!f || getuid() == 0 || fgetc(f) != 'x') return 1;
    if (!access("/workspace/unreviewed.txt", F_OK) || !access("/var/run/docker.sock", F_OK)) return 2;
    if (fopen("/workspace/new-file","w") || fopen("/host-write","w")) return 3;
    return 0;
}
""")
        subprocess.run(["cc", "-static", "-O2", "-o", "check", "probe.c"], cwd=build, check=True)
        (build / "Dockerfile").write_text("FROM scratch\nCOPY check /check\n")
        subprocess.run(["docker", "build", "--network=none", "--iidfile", "image-id", "."], cwd=build, check=True)
        image = (build / "image-id").read_text().strip()
        try:
            (project / "input.txt").write_text("x")
            (project / "unreviewed.txt").write_text("local-only fixture")
            command = ["/check"]
            with mock.patch.object(close_review, "_home", return_value=Path(owner)):
                path = close_review._policy_path(project, command)
                close_review._review_store(path, project, create=True)
                record = {"image": image, "inputs": ["input.txt"], "digest": close_review.snapshot(project, ["input.txt"])}
                path.write_text(json.dumps(record))
                path.chmod(0o600)
                code, _ = close_review.execute(command, project, 60)
                if code:
                    raise RuntimeError("Docker isolation check failed, status %d" % code)
        finally:
            subprocess.run(["docker", "image", "rm", image], check=False)


if __name__ == "__main__":
    main()
