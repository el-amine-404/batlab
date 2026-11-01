#!/usr/bin/env python3
"""Updates the package-lock.json a Renovate branch left behind, for deps-gate.

Renovate changes package.json in some of these apps without updating the lock
beside it, and `npm ci` then fails the build: every frontend update stayed
unmerged that way (2026-11-01). Commits what it repairs and exits 0; prints
"repaired=true" for the workflow when it did. Scripts of packages do not run
here (--ignore-scripts), and this needs no token.
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def node_image():
    env = (ROOT / "compose/versions.env").read_text()
    # The node the apps build with, so the lock is written by the same npm.
    return "node:" + re.search(r"^TAG_SUBFEED_NODE=(.+)$", env, re.M).group(1)


def stale_locks(base):
    changed = set(git("diff", "--name-only", base, "HEAD").splitlines())
    for path in sorted(changed):
        if path.endswith("/package.json"):
            lock = path.removesuffix("package.json") + "package-lock.json"
            if lock not in changed and (ROOT / lock).exists():
                yield lock


def main():
    base = git("merge-base", sys.argv[1] if len(sys.argv) > 1 else "origin/main", "HEAD").strip()
    repaired = []
    for lock in stale_locks(base):
        folder = (ROOT / lock).parent
        subprocess.run(["docker", "run", "--rm", "--network", "host", "--user", f"{os.getuid()}:{os.getgid()}",
                        "-e", "HOME=/tmp", "-v", f"{folder}:/work", "-w", "/work", node_image(),
                        "npm", "install", "--package-lock-only", "--ignore-scripts",
                        "--no-audit", "--no-fund", "--loglevel=error"], check=True)
        if git("status", "--porcelain", "--", lock).strip():
            git("add", "--", lock)
            repaired.append(lock)
            print(f"Updated {lock}")
    if repaired:
        git("-c", "user.name=deps-gate", "-c", "user.email=deps-gate@users.noreply.github.com",
            "commit", "-q", "-m", "chore: update the lock files Renovate left behind\n\n" + "\n".join(repaired))
        print("repaired=true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
