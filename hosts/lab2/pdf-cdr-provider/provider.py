#!/usr/bin/env python3
"""Host-side Dangerzone CDR provider for the PDF gateway.

Runs as the rootless Dangerzone account. It has no database, broker, or
quarantine access: the gateway's cdr-worker streams source bytes over a Unix
socket and receives either a bounded PDF or a typed failure. One conversion runs
at a time. Every conversion must prove the verified isolation boundary from
Dangerzone's own debug output; otherwise the output is discarded.

Protocol pdf-cdr-1 (both directions start with one JSON line, at most 4 KiB):
  request:  {"protocol":"pdf-cdr-1","jobId":"scan-...","sha256":"<hex>","bytes":N} + N bytes
  response: {"protocol":"pdf-cdr-1","status":"OK",...,"outputBytes":M} + M bytes
            {"protocol":"pdf-cdr-1","status":"FAILED","code":"<CODE>",...}
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROTOCOL = "pdf-cdr-1"
PROVIDER_VERSION = "pdf-cdr-provider-1"
MAX_HEADER = 4096
MAX_INPUT = 50 * 1024 * 1024
MAX_DEBUG_LOG = 64 * 1024 * 1024
JOB_ID = re.compile(r"^scan-[0-9A-HJKMNP-TV-Z]{26}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_NAME = re.compile(r"--name[= ](dangerzone-doc-to-pixels-[A-Za-z0-9_-]+)")
IMAGE = "ghcr.io/freedomofpress/dangerzone/v1"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TOOL_ENV_KEYS = ("HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "CONTAINERS_CONF")


class Failure(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail[:300]


def log(event: str, **fields: object) -> None:
    # Structured, content-free operational log for journald.
    print(json.dumps({"event": event, **fields}, sort_keys=True), flush=True)


def tool_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in TOOL_ENV_KEYS if key in os.environ}
    env["PATH"] = "/usr/bin:/bin"
    env["LANG"] = "C.UTF-8"
    return env


def run_tool(argv: list[str], timeout: int = 60) -> str:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False, env=tool_env())
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Failure("CAPABILITY_UNAVAILABLE", f"{argv[0]}: {type(error).__name__}") from error
    if result.returncode != 0:
        raise Failure("CAPABILITY_UNAVAILABLE", f"{Path(argv[0]).name} exited {result.returncode}")
    return result.stdout.decode("utf-8", "replace")


def capability(expected_digest: str | None) -> dict:
    if os.geteuid() == 0:
        raise Failure("CAPABILITY_UNAVAILABLE", "Dangerzone must run rootless")
    conf = os.environ.get("CONTAINERS_CONF", "")
    if not conf or not os.access(conf, os.R_OK):
        raise Failure("CAPABILITY_UNAVAILABLE", "dedicated CONTAINERS_CONF is not readable")
    runtime = run_tool(["/usr/bin/podman", "info", "--format", "{{.Host.OCIRuntime.Name}}"]).strip()
    if runtime != "crun":
        raise Failure("CAPABILITY_UNAVAILABLE", f"outer runtime is {runtime[:40]!r}, expected crun")
    run_tool(["/usr/bin/dangerzone-image", "verify-local"], timeout=120)
    version = run_tool(["/usr/bin/dangerzone-cli", "--version"]).strip()[:40]
    digests = run_tool(["/usr/bin/podman", "image", "list", "--format", "{{.Digest}}",
                        "ghcr.io/freedomofpress/dangerzone/v1"]).split()
    digests = sorted({item for item in digests if DIGEST.match(item)})
    if len(digests) != 1:
        raise Failure("CAPABILITY_UNAVAILABLE", "expected exactly one local sandbox image digest")
    if expected_digest and digests[0] != expected_digest:
        raise Failure("CAPABILITY_UNAVAILABLE", "sandbox image digest is not the pinned digest")
    return {"dangerzoneVersion": version, "imageDigest": digests[0], "outerRuntime": runtime}


def isolation_evidence(debug_log: Path, image_digest: str) -> dict:
    """Derive isolation evidence from the exact commands Dangerzone ran."""
    podman_runs: list[list[str]] = []
    runsc_lines: list[str] = []
    with debug_log.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped.startswith("Running: /usr/bin/podman run "):
                try:
                    podman_runs.append(shlex.split(stripped.removeprefix("Running: ")))
                except ValueError as error:
                    raise Failure("ISOLATION_EVIDENCE_MISSING", "unparseable podman command") from error
            elif stripped.startswith("Running gVisor with command line: "):
                runsc_lines.append(stripped)
    if len(podman_runs) != 1:
        raise Failure("ISOLATION_EVIDENCE_MISSING", f"expected one sandbox launch, saw {len(podman_runs)}")
    argv = podman_runs[0]
    image_ref = f"{IMAGE}@{image_digest}"
    if image_ref not in argv:
        raise Failure("ISOLATION_EVIDENCE_MISSING", "sandbox was not launched from the verified image digest")
    # Only options before the image belong to podman; the rest is the sandbox command.
    options = argv[2:argv.index(image_ref)]
    pairs = {(options[i], options[i + 1]) for i in range(len(options) - 1)}
    for pair in (("--security-opt", "no-new-privileges"), ("--cap-drop", "all"), ("--userns", "nomap"), ("-u", "dangerzone")):
        if pair not in pairs:
            raise Failure("ISOLATION_EVIDENCE_MISSING", f"sandbox launch lacks {pair[0]} {pair[1]}")
    for flag in ("--network=none", "--rm", "-i"):
        if flag not in options:
            raise Failure("ISOLATION_EVIDENCE_MISSING", f"sandbox launch lacks {flag}")
    for arg in options:
        if arg in ("-v", "--volume", "--mount", "--privileged", "--network", "--net", "--pid", "--ipc") \
                or arg.startswith(("--volume=", "-v=", "--net=", "--pid=", "--ipc=", "--privileged=")):
            raise Failure("ISOLATION_EVIDENCE_MISSING", f"sandbox launch has forbidden option {arg[:40]}")
        if arg.startswith("--mount=") and not arg.startswith("--mount=type=tmpfs,"):
            raise Failure("ISOLATION_EVIDENCE_MISSING", "sandbox launch has a non-tmpfs mount")
        if arg.startswith("--network=") and arg != "--network=none":
            raise Failure("ISOLATION_EVIDENCE_MISSING", "sandbox launch has network access")
    if not any(" /usr/bin/runsc " in f" {line} " and "--network=none" in line.split() for line in runsc_lines):
        raise Failure("ISOLATION_EVIDENCE_MISSING", "inner gVisor launch was not observed")
    return {"outerNetwork": "none", "outerCapabilities": "all-dropped-except-SYS_CHROOT", "noNewPrivileges": True,
            "hostMounts": "none", "innerSandbox": "gVisor runsc", "innerNetwork": "none",
            "sourceTransfer": "stdin", "imageReference": image_ref}


def kill_sandbox(debug_log: Path) -> None:
    """Kill exactly the sandbox container this conversion named."""
    names = set()
    if debug_log.exists():
        with debug_log.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                names.update(CONTAINER_NAME.findall(line))
    for name in names:
        subprocess.run(["/usr/bin/podman", "kill", name], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False, env=tool_env())
        subprocess.run(["/usr/bin/podman", "rm", "-f", name], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False, env=tool_env())
        left = subprocess.run(["/usr/bin/podman", "ps", "-a", "-q", "--filter", f"name=^{name}$"],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=30, check=False, env=tool_env())
        if left.returncode != 0 or left.stdout.strip():
            raise Failure("CLEANUP_FAILED", "sandbox container survived termination")


def convert(source: Path, workdir: Path, args: argparse.Namespace, image_digest: str) -> tuple[Path, dict]:
    output = workdir / "output.pdf"
    debug_log = workdir / "dangerzone-debug.log"
    # No RLIMIT_FSIZE: gVisor's sparse memory file would exceed it. Host-side
    # files are bounded by polling instead.
    started = time.monotonic()
    with debug_log.open("wb") as log_handle:
        process = subprocess.Popen(["/usr/bin/dangerzone-cli", "--debug", "-o", str(output), str(source)],
                                   stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
                                   cwd=workdir, env=tool_env(), start_new_session=True)
        failure = None
        while process.poll() is None:
            if time.monotonic() - started > args.timeout:
                failure = Failure("TIMEOUT", f"conversion exceeded {args.timeout}s")
            elif debug_log.stat().st_size > MAX_DEBUG_LOG:
                failure = Failure("CONVERSION_FAILED", "debug log exceeded its bound")
            elif output.exists() and output.stat().st_size > args.max_output_bytes:
                failure = Failure("OUTPUT_TOO_LARGE", f"output exceeds {args.max_output_bytes} bytes")
            if failure:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                kill_sandbox(debug_log)
                raise failure
            time.sleep(0.2)
        returncode = process.returncode
    duration_ms = int((time.monotonic() - started) * 1000)
    if returncode != 0:
        kill_sandbox(debug_log)
        raise Failure("CONVERSION_FAILED", f"dangerzone-cli exited {returncode}")
    evidence = isolation_evidence(debug_log, image_digest)
    if not output.is_file() or output.is_symlink():
        raise Failure("OUTPUT_INVALID", "no regular output file")
    size = output.stat().st_size
    if size <= 0:
        raise Failure("OUTPUT_INVALID", "empty output")
    if size > args.max_output_bytes:
        raise Failure("OUTPUT_TOO_LARGE", f"output exceeds {args.max_output_bytes} bytes")
    with output.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise Failure("OUTPUT_INVALID", "output is not a PDF")
    return output, {"durationMs": duration_ms, "isolation": evidence}


def read_line(conn: socket.socket) -> dict:
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = conn.recv(1)
        if not chunk:
            raise Failure("INPUT_INVALID", "connection closed before header")
        data.extend(chunk)
        if len(data) > MAX_HEADER:
            raise Failure("INPUT_INVALID", "header too large")
    try:
        value = json.loads(data)
    except json.JSONDecodeError as error:
        raise Failure("INPUT_INVALID", "header is not JSON") from error
    if not isinstance(value, dict):
        raise Failure("INPUT_INVALID", "header is not an object")
    return value


def send_header(conn: socket.socket, header: dict) -> None:
    encoded = json.dumps({"protocol": PROTOCOL, "providerVersion": PROVIDER_VERSION, **header},
                         sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_HEADER:
        encoded = json.dumps({"protocol": PROTOCOL, "status": "FAILED", "code": "INTERNAL_ERROR"}).encode() + b"\n"
    conn.sendall(encoded)


def handle(conn: socket.socket, args: argparse.Namespace) -> None:
    pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    if uid not in args.allowed_uid:
        log("peer_rejected", uid=uid)
        send_header(conn, {"status": "FAILED", "code": "PEER_NOT_ALLOWED"})
        return
    conn.settimeout(args.io_timeout)
    job_id = None
    workdir = Path(tempfile.mkdtemp(prefix="job-", dir=args.scratch))
    try:
        os.chmod(workdir, 0o700)
        request = read_line(conn)
        job_id = request.get("jobId")
        expected_hash = request.get("sha256")
        length = request.get("bytes")
        if (request.get("protocol") != PROTOCOL or not isinstance(job_id, str) or not JOB_ID.match(job_id)
                or not isinstance(expected_hash, str) or not SHA256.match(expected_hash)
                or not isinstance(length, int) or isinstance(length, bool) or not 0 < length <= MAX_INPUT):
            raise Failure("INPUT_INVALID", "request header failed validation")
        source = workdir / "input.pdf"
        digest = hashlib.sha256()
        remaining = length
        with source.open("xb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            while remaining:
                chunk = conn.recv(min(1024 * 1024, remaining))
                if not chunk:
                    raise Failure("INPUT_INVALID", "source stream ended early")
                digest.update(chunk)
                handle.write(chunk)
                remaining -= len(chunk)
        if digest.hexdigest() != expected_hash:
            raise Failure("INPUT_INVALID", "source SHA-256 mismatch")
        log("conversion_started", jobId=job_id, bytes=length)
        conn.settimeout(None)
        caps = capability(args.expected_image_digest)
        output, details = convert(source, workdir, args, caps["imageDigest"])
        size = output.stat().st_size
        out_digest = hashlib.sha256()
        with output.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                out_digest.update(chunk)
        conn.settimeout(args.io_timeout)
        send_header(conn, {"status": "OK", "jobId": job_id, "sourceSha256": expected_hash,
                           "outputSha256": out_digest.hexdigest(), "outputBytes": size, **caps, **details})
        with output.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                conn.sendall(chunk)
        log("conversion_succeeded", jobId=job_id, durationMs=details["durationMs"], outputBytes=size)
    except Failure as failure:
        log("conversion_failed", jobId=job_id, code=failure.code, detail=failure.detail)
        try:
            conn.settimeout(args.io_timeout)
            send_header(conn, {"status": "FAILED", "code": failure.code, "detail": failure.detail, "jobId": job_id})
        except OSError:
            pass
        if failure.code == "CLEANUP_FAILED":
            # A surviving sandbox means the host state is no longer known-good.
            raise
    except OSError as error:
        log("connection_failed", jobId=job_id, errorType=type(error).__name__)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--allowed-uid", type=int, action="append", required=True)
    parser.add_argument("--scratch", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--io-timeout", type=int, default=60)
    parser.add_argument("--max-output-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--expected-image-digest", default=None)
    args = parser.parse_args()
    if args.expected_image_digest and not DIGEST.match(args.expected_image_digest):
        parser.error("--expected-image-digest must be sha256:<64 hex>")
    os.umask(0o077)
    if args.scratch is None:
        args.scratch = Path(tempfile.mkdtemp(prefix="pdf-cdr-provider-"))
    args.scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(args.scratch, 0o700)

    path = args.socket
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit(f"refusing to replace non-socket {path}")
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    # Access is enforced with SO_PEERCRED; the mode lets the container UID connect.
    os.chmod(path, 0o666)
    server.listen(1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    log("provider_ready", socket=str(path), allowedUids=args.allowed_uid, timeout=args.timeout)
    try:
        while True:
            conn, _ = server.accept()
            with conn:
                handle(conn, args)
    except Failure as failure:
        log("provider_stopped", code=failure.code)
        return 3
    finally:
        server.close()
        try:
            path.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
