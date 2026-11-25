"""Owner-reviewed, isolated close checks. Run this tool from the trusted plugin installation."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


class ReviewError(ValueError):
    pass


def _home():
    # Keep authorization outside a project and outside its configurable XDG environment.
    if os.name != "posix":
        raise ReviewError("owner review required; automatic verification needs a POSIX host with Docker")
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _policy_path(root, command):
    identity = json.dumps([str(Path(root).resolve()), command], ensure_ascii=True).encode()
    return _home() / ".sotacommand-close" / (hashlib.sha256(identity).hexdigest() + ".json")


def _review_store(path, root, create=False):
    folder = path.parent
    try:
        folder.resolve().relative_to(Path(root).resolve())
    except ValueError:
        pass
    else:
        raise ReviewError("authorization must be outside the project")
    if create:
        folder.mkdir(mode=0o700, exist_ok=True)
    for item in (folder, path) if path.exists() or path.is_symlink() else (folder,):
        info = item.lstat()
        expected = stat.S_ISDIR if item == folder else stat.S_ISREG
        if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ReviewError("authorization must be an owner-only regular record in an owner-only directory")


def _file(root, relative):
    if not isinstance(relative, str):
        raise ReviewError("review inputs must be project-relative files")
    path = PurePosixPath(relative)
    parts = relative.split("/")
    if path.is_absolute() or any(p in ("", ".", "..", ".git") or "\\" in p or ":" in p for p in parts):
        raise ReviewError("review inputs must be project-relative files")
    src = root
    for part in parts:
        src = src / part
        if src.is_symlink():
            raise ReviewError("review inputs may not contain links")
    return src


def snapshot(root, inputs, destination=None):
    if not isinstance(inputs, list) or not inputs or len(inputs) > 10000:
        raise ReviewError("select between 1 and 10000 review input files")
    root = Path(root).resolve(strict=True)
    digest = hashlib.sha256()
    total = 0
    for relative in sorted(set(inputs)):
        src = _file(root, relative)
        before = src.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReviewError("review inputs must be regular, single-link files")
        total += before.st_size
        if total > 32 * 1024 * 1024:
            raise ReviewError("review input exceeds 32 MiB")
        with os.fdopen(os.open(src, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as file:
            if not os.path.samestat(before, os.fstat(file.fileno())):
                raise ReviewError("review inputs changed")
            data = file.read(before.st_size + 1)
        if len(data) != before.st_size:
            raise ReviewError("review inputs changed")
        mode = 0o555 if before.st_mode & 0o111 else 0o444
        entry = [relative, mode, hashlib.sha256(data).hexdigest()]
        digest.update(json.dumps(entry, ensure_ascii=True).encode() + b"\n")
        if destination:
            dest = destination.joinpath(*relative.split("/"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(mode)
    return digest.hexdigest()


def execute(command, root, timeout):
    path = _policy_path(root, command)
    try:
        _review_store(path, root)
        record = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise ReviewError("owner review required for this project and command")
    image = record.get("image", "")
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ReviewError("review must pin a local Docker image ID")
    docker = shutil.which("docker", path="/usr/local/bin:/usr/bin:/opt/homebrew/bin")
    if not docker:
        raise ReviewError("Docker is unavailable; verification stayed off")
    with tempfile.TemporaryDirectory(prefix="sotacommand-check-") as scratch:
        scratch = Path(scratch)
        # Only reviewed bytes enter the container. No checkout, host environment, socket or credentials are mounted.
        workspace = scratch / "reviewed"
        workspace.mkdir(mode=0o755)
        current = snapshot(root, record["inputs"], workspace)
        if current != record["digest"]:
            raise ReviewError("reviewed inputs changed; renew owner authorization")
        if any(c in str(workspace) for c in (",", "\n", "\r")):
            raise ReviewError("unsupported temporary directory for isolated verification")
        docker_config = scratch / "docker"
        docker_config.mkdir(mode=0o700)
        cid = scratch / "container-id"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(scratch), "DOCKER_CONFIG": str(docker_config)}
        base = [docker, "--host", "unix:///var/run/docker.sock"]
        argv = command if isinstance(command, list) else ["/bin/sh", "-c", command]
        run = base + ["run", "--pull=never", "--rm", "--cidfile", str(cid), "--network=none",
                      "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=96",
                      "--memory=512m", "--cpus=1", "--user=65534:65534", "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
                      "--mount", "type=bind,src=" + str(workspace) + ",dst=/workspace,readonly",
                      "--workdir=/workspace", "--entrypoint", argv[0], image] + argv[1:]
        try:
            result = subprocess.run(run, env=env, cwd=scratch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=timeout, check=False)
            return result.returncode, ""
        finally:
            if cid.is_file():
                container = cid.read_text().strip()
                if re.fullmatch("[0-9a-f]{64}", container):
                    subprocess.run(base + ["rm", "-f", container], env=env, cwd=scratch, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--command-json", required=True, help="the configured command as a JSON string or argument array")
    parser.add_argument("--image", required=True, help="reviewed local Docker image ID, sha256: followed by 64 hex digits")
    parser.add_argument("--input", action="append", required=True, help="one reviewed regular file; repeat for each input")
    parser.add_argument("--approve-digest", help="save authorization only when this digest matches, from an owner terminal")
    args = parser.parse_args()
    command = json.loads(args.command_json)
    if not (isinstance(command, str) and command or isinstance(command, list) and command and all(isinstance(p, str) and p for p in command)):
        parser.error("supply a nonempty command")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image):
        parser.error("pin the full local Docker image ID")
    digest = snapshot(args.root, args.input)
    print("Reviewed input digest:", digest)
    if args.approve_digest:
        if not os.isatty(0) or digest != args.approve_digest:
            parser.error("approval requires an owner terminal and the exact reviewed digest")
        path = _policy_path(args.root, command)
        _review_store(path, args.root, create=True)
        record = {"image": args.image, "digest": digest, "inputs": sorted(set(args.input))}
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(json.dumps(record).encode())
        os.replace(temp.name, path)
        print("Saved owner authorization for this command and input snapshot.")


if __name__ == "__main__":
    main()
