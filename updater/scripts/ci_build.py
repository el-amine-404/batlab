#!/usr/bin/env python3
"""Builds the apps a Renovate branch touches, for the deps-gate workflow.

Each app's Dockerfile runs its tests and typecheck, so a build that passes is
the same check `make up` makes on the lab, without the lab's CPU. Prints the
images it would build with --list. Exits 0 when the branch touches no app.
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SHARED = ("lib/batlab-common/", "lib/batlab-ui/")


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def changed_pins(base):
    names = set()
    for line in git("diff", "-U0", base, "HEAD", "--", "compose/versions.env").splitlines():
        found = re.match(r"^[+-]([A-Z0-9_]+)=", line)
        if found:
            names.add(found.group(1))
    return names


def render(compose_file, stub):
    """The stack as Compose sees it, with a placeholder for each required secret."""
    # Only what no env file gives a value: read last, a placeholder would replace a real
    # value, and "ci" in a number such as cpus stops Compose from rendering at all.
    given = set()
    for env in ("compose/versions.env", "compose/.env.example", "hosts/lab2/compose.env"):
        given |= set(re.findall(r"^([A-Z0-9_]+)=\S", (ROOT / env).read_text(), re.M))
    required = set(re.findall(r"\$\{([A-Z0-9_]+):\?", compose_file.read_text())) - given
    stub.write_text("".join(f"{name}=ci\n" for name in sorted(required)))
    result = subprocess.run(
        ["docker", "compose", "--profile", "*",
         "--env-file", "compose/versions.env", "--env-file", "compose/.env.example",
         "--env-file", "hosts/lab2/compose.env", "--env-file", str(stub),
         "-f", str(compose_file), "config", "--format", "json"],
        cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)["services"]


def build_command(service):
    build = service["build"]
    context = build["context"]
    argv = ["docker", "build", "--network", "host", "-f",
            str(pathlib.Path(context) / build.get("dockerfile", "Dockerfile"))]
    for key, value in (build.get("args") or {}).items():
        argv += ["--build-arg", f"{key}={value}"]
    for key, value in (build.get("additional_contexts") or {}).items():
        argv += ["--build-context", f"{key}={value}"]
    if build.get("target"):
        argv += ["--target", build["target"]]
    return [*argv, context]


def main():
    args = [a for a in sys.argv[1:] if a != "--list"]
    only_list = "--list" in sys.argv
    base = args[0] if args else "origin/main"
    base = git("merge-base", base, "HEAD").strip()
    changed = git("diff", "--name-only", base, "HEAD").splitlines()
    pins = changed_pins(base)
    shared = any(path.startswith(SHARED) for path in changed)

    commands, seen = [], set()
    with tempfile.TemporaryDirectory() as scratch:
        stub = pathlib.Path(scratch) / "stub.env"
        for compose_file in sorted((ROOT / "compose").glob("*/docker-compose.yml")):
            stack = compose_file.parent.name
            text = compose_file.read_text()
            if "build:" not in text:
                continue
            touched = (any(path.startswith(f"compose/{stack}/") for path in changed)
                       or (shared and "lib/batlab-" in text)
                       or any("${" + pin in text for pin in pins))
            if not touched:
                continue
            for name, service in render(compose_file, stub).items():
                if not service.get("build"):
                    continue
                command = build_command(service)
                if tuple(command) not in seen:
                    seen.add(tuple(command))
                    commands.append((f"{stack}/{name}", command))
            if stack == "pdf-gateway":
                commands.append(("pdf-gateway/shapes-sandbox",
                                 ["docker", "build", "--network", "host",
                                  str(ROOT / "compose/pdf-gateway/app/shapes-sandbox")]))

    if not commands:
        print("This branch touches no app built here.")
    failed = []
    for label, command in commands:
        print(f"::group::{label}" if not only_list else label, flush=True)
        if not only_list and subprocess.run(command, cwd=ROOT).returncode:
            failed.append(label)
        if not only_list:
            print("::endgroup::", flush=True)
    if failed:
        print("Failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
