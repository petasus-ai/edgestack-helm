#!/usr/bin/env python3
"""Bring index.yaml in line with the charts on this branch (run by update-index.yml).

The whole branch is compared with its index on every run, so a run that was cancelled or
lost a push race leaves nothing behind: the next one finds it.

master (stable channel), append-only:
  - a chart file the index does not list yet is added to it;
  - a chart file whose digest differs from its index entry, or an entry whose file is
    gone, is a change to a published version: a chart listed in .github/unguarded.txt
    (still republished under one version by its repository), or any chart when the list
    holds "*", is indexed again; any other is restored from history to the contents its
    index entry names;
  - a 0.0.0-dev chart belongs on dev and is removed.
  New and changed entries are merged into the index (helm repo index --merge), so the
  others keep their creation time; a removed entry reindexes everything.
dev (dev channel): 0.0.0-dev charts only, anything else is removed; the index is rebuilt
  when it does not list exactly the files there.

Exits 1 when something had to be restored or removed; the caller commits either way.
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

import yaml

DEV = "0.0.0-dev"


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def chart_meta(data):
    """name and version from the Chart.yaml inside a chart archive"""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.count("/") == 1 and member.name.endswith("/Chart.yaml"):
                chart = yaml.safe_load(tar.extractfile(member))
                return chart["name"], str(chart["version"])
    raise ValueError("no Chart.yaml")


def restore(path, digest):
    """put back the contents of path whose sha256 is digest, from the branch's history"""
    for commit in git("log", "--format=%H", "--", path).decode().split():
        try:
            data = git("show", f"{commit}:{path}")
        except subprocess.CalledProcessError:
            continue
        if hashlib.sha256(data).hexdigest() == digest:
            with open(path, "wb") as f:
                f.write(data)
            git("add", "--", path)
            return True
    return False


def main():
    branch = os.environ["BRANCH"]
    unguarded = set()
    if os.path.exists(".github/unguarded.txt"):
        for line in open(".github/unguarded.txt"):
            line = line.split("#")[0].strip()
            if line:
                unguarded.add(line)

    index = yaml.safe_load(open("index.yaml")) if os.path.exists("index.yaml") else {}
    listed = {}  # file -> (name, version, digest)
    for name, entries in ((index or {}).get("entries") or {}).items():
        for e in entries:
            for url in e.get("urls", []):
                listed[os.path.basename(url)] = (name, str(e["version"]), e.get("digest", ""))

    files = {}
    for path in sorted(p for p in os.listdir(".") if p.endswith(".tgz")):
        data = open(path, "rb").read()
        files[path] = (*chart_meta(data), hashlib.sha256(data).hexdigest())

    problems, merge, reindex_all = [], [], False

    if branch == "dev":
        for path, (name, version, _) in files.items():
            if version != DEV:
                problems.append(f"{path}: {name} {version} on dev, which takes {DEV} only; removed")
                git("rm", "-q", "--", path)
        kept = {p: v for p, v in files.items() if v[1] == DEV}
        reindex_all = {p: v[2] for p, v in kept.items()} != {p: v[2] for p, v in listed.items()}
    else:
        for path, (name, version, digest) in files.items():
            if version == DEV:
                problems.append(f"{path}: {DEV} belongs on the dev branch; removed")
                git("rm", "-q", "--", path)
            elif path not in listed:
                merge.append(path)
            elif listed[path][2] != digest:
                if "*" in unguarded or name in unguarded:
                    merge.append(path)
                elif restore(path, listed[path][2]):
                    problems.append(f"{path}: {name} {version} is published and does not change; restored")
                else:
                    problems.append(f"{path}: {name} {version} changed and its published contents are not in history")
        for path, (name, version, digest) in listed.items():
            if path in files:
                continue
            if "*" in unguarded or name in unguarded:
                reindex_all = True
            elif restore(path, digest):
                problems.append(f"{path}: {name} {version} is published and stays; restored")
            else:
                problems.append(f"{path}: {name} {version} is gone and its contents are not in history")
                reindex_all = True

    if reindex_all or not os.path.exists("index.yaml"):
        subprocess.run(["helm", "repo", "index", "."], check=True)
    elif merge:
        with tempfile.TemporaryDirectory() as tmp:
            for path in merge:
                # copies, here and below: the runner's temporary directory is on another file
                # system, where links and renames fail
                shutil.copy(path, os.path.join(tmp, path))
            # the new entries win over the old ones of the same version
            subprocess.run(["helm", "repo", "index", tmp, "--merge", "index.yaml"], check=True)
            shutil.copyfile(os.path.join(tmp, "index.yaml"), "index.yaml")

    for p in problems:
        print(f"::error::{p}")
    print(f"{branch}: {len(merge)} merged, full reindex {reindex_all}, {len(problems)} rejected")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
