#!/usr/bin/env python3
"""Renovate branches the deps-gate workflow refused: branch, commit, link, per line.

The repository is public, so GitHub's API needs no token. Prints nothing on any
network or API error: an outage is not a stuck update.
"""
import json
import subprocess
import sys
import urllib.request

REPO = "el-amine-404/batlab"


def main():
    refs = subprocess.run(["git", "ls-remote", "origin", "refs/heads/renovate/*"],
                          check=True, capture_output=True, text=True).stdout
    for line in refs.splitlines():
        sha, ref = line.split("\t")
        url = f"https://api.github.com/repos/{REPO}/commits/{sha}/check-runs"
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=20) as answer:
            runs = json.load(answer)["check_runs"]
        for run in runs:
            if run["conclusion"] == "failure":
                print(ref.removeprefix("refs/heads/renovate/"), sha, run["html_url"], sep="\t")
                break


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        print(f"stuck_updates: {error}", file=sys.stderr)
