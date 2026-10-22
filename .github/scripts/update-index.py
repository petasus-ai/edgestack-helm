#!/usr/bin/env python3
"""Check the charts a push changed and index them (run by update-index.yml).

master (stable channel): append-only. A chart not listed in .github/unguarded.txt may
not be modified or deleted; such a change is reverted. 0.0.0-dev charts belong on dev and
are removed. index.yaml is updated for the changed files only (helm repo index --merge),
so the other entries keep their digest and creation time; a deletion (an unguarded chart)
reindexes everything.

dev (dev channel): only 0.0.0-dev charts, which may be overwritten; anything else is
removed. index.yaml is rebuilt.

The fixes and the index go in one commit, pushed with retries; the run fails if
anything had to be reverted or removed.
"""
import io
import os
import subprocess
import sys
import tarfile
import tempfile

import yaml

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
DEV = "0.0.0-dev"


def git(*args, capture=True):
    out = subprocess.run(["git", *args], check=True, capture_output=capture)
    return out.stdout if capture else None


def chart_meta(data):
    """name and version from the Chart.yaml inside a chart archive"""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.count("/") == 1 and member.name.endswith("/Chart.yaml"):
                chart = yaml.safe_load(tar.extractfile(member))
                return chart["name"], str(chart["version"])
    raise ValueError("no Chart.yaml")


def main():
    branch = os.environ["BRANCH"]
    before = os.environ.get("BEFORE", "")
    base = before if before and set(before) != {"0"} else EMPTY_TREE
    unguarded = set()
    if os.path.exists(".github/unguarded.txt"):
        for line in open(".github/unguarded.txt"):
            line = line.split("#")[0].strip()
            if line:
                unguarded.add(line)

    changes = []
    for line in git("diff", "--name-status", "--no-renames", base, "HEAD", "--", "*.tgz").decode().splitlines():
        status, path = line.split("\t", 1)
        changes.append((status[0], path))

    problems, indexed = [], []
    for status, path in changes:
        if status == "D":
            name, version = chart_meta(git("show", f"{base}:{path}"))
        else:
            name, version = chart_meta(open(path, "rb").read())
        if branch == "dev":
            if status != "D" and version != DEV:
                problems.append(f"{path}: {name} {version} on dev, which takes {DEV} only; removed")
                git("rm", "-q", "--", path, capture=False)
                continue
        else:
            if status == "A" and version == DEV:
                problems.append(f"{path}: {DEV} belongs on the dev branch; removed")
                git("rm", "-q", "--", path, capture=False)
                continue
            if status in "MD" and name not in unguarded:
                problems.append(f"{path}: {name} {version} is published and does not change; restored")
                git("checkout", base, "--", path, capture=False)
                continue
        if status != "D":
            indexed.append(path)

    deleted = any(status == "D" for status, _ in changes)
    if branch == "dev" or deleted or not os.path.exists("index.yaml"):
        # dev, or an unguarded chart was deleted: index every file again (the creation
        # times are renewed; the digests stay)
        subprocess.run(["helm", "repo", "index", "."], check=True)
    elif indexed:
        with tempfile.TemporaryDirectory() as tmp:
            for path in indexed:
                os.link(path, os.path.join(tmp, os.path.basename(path)))
            # the new entries win over the old ones of the same version (an unguarded
            # chart republished); all others are kept as they are
            subprocess.run(["helm", "repo", "index", tmp, "--merge", "index.yaml"], check=True)
            os.replace(os.path.join(tmp, "index.yaml"), "index.yaml")

    for p in problems:
        print(f"::error::{p}")
    print(f"{len(indexed)} charts indexed on {branch}; {len(problems)} rejected")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
