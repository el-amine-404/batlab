#!/usr/bin/env python3
"""Keep gluetun's forwarded port alive and qBittorrent announcing it.

On 2026-10-03 gluetun restarted the tunnel after a failed health check and
landed on a Proton server that answered the port-forwarding request with "not
authorized". gluetun does not retry that, so the forwarded port stayed 0 and
qBittorrent could not be reached until someone reconnected by hand.

Every run (a timer, every five minutes):

  port 0 twice in a row while the tunnel runs
      reconnect through gluetun's control server, which picks another server
      from the port-forwarding pool; at most three times an hour
  port changed since the last run (a reconnect, ours or gluetun's)
      ask qBittorrent to re-announce every torrent, so trackers stop handing
      out the old port for up to half an hour

Each reconnect, and giving up for the hour, is posted to Discord.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CONTROL = "http://127.0.0.1:8000"
QBITTORRENT = "http://127.0.0.1:8080"
ZERO_RUNS_BEFORE_RECONNECT = 2
MAX_RECONNECTS_PER_HOUR = 3
PORT_WAIT_SECONDS = 120


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


CONTROL_KEY = ""   # GLUETUN_CONTROL_API_KEY, read in main()


def container_http(container: str, url: str, method: str = "GET", body: str | None = None, key: str = "") -> str:
    """An HTTP call made inside a container, whose localhost is gluetun's
    network namespace: the control server and qBittorrent listen only there."""
    command = ["docker", "exec", container, "wget", "-qO-", "-T", "15"]
    if key:
        command += [f"--header=X-API-Key: {key}"]
    if method == "POST":
        # A form post, which is what qBittorrent's API takes.
        command += [f"--post-data={body or ''}"]
    elif method != "GET":
        command += [f"--method={method}", "--header=Content-Type: application/json", f"--body-data={body or ''}"]
    result = subprocess.run(command + [url], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"{container}: {url}: {result.stderr.strip() or 'request failed'}")
    return result.stdout


def tunnel_running() -> bool:
    return json.loads(container_http("gluetun", f"{CONTROL}/v1/vpn/status")).get("status") == "running"


def forwarded_port() -> int:
    return int(json.loads(container_http("gluetun", f"{CONTROL}/v1/portforward")).get("port") or 0)


def reconnect() -> int:
    """Stops and starts the tunnel, then waits for a forwarded port (0 if none)."""
    for status in ("stopped", "running"):
        # The one route gluetun's auth config (compose/arr/conf/gluetun/auth.toml.example) keeps behind a key.
        container_http("gluetun", f"{CONTROL}/v1/vpn/status", "PUT", json.dumps({"status": status}), key=CONTROL_KEY)
        time.sleep(3)
    deadline = time.monotonic() + PORT_WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(10)
        try:
            port = forwarded_port()
        except (RuntimeError, ValueError):
            continue
        if port:
            return port
    return 0


def reannounce() -> None:
    container_http("qbittorrent", f"{QBITTORRENT}/api/v2/torrents/reannounce", "POST", "hashes=all")


@dataclass
class State:
    zero_runs: int = 0
    last_port: int = 0
    reconnects: list[float] = field(default_factory=list)
    gave_up_at: float = 0.0


@dataclass
class Plan:
    reconnect: bool = False
    reannounce: bool = False
    give_up: bool = False


def decide(state: State, running: bool, port: int, now: float) -> Plan:
    """What to do this run; updates state's counters but not the outcome of a reconnect."""
    state.reconnects = [at for at in state.reconnects if now - at < 3600]
    plan = Plan()
    if not running:
        # gluetun's own health check restarts a dead tunnel, and Uptime Kuma
        # reports it; a port can only be forwarded through a running one.
        state.zero_runs = 0
        return plan
    if port:
        state.zero_runs = 0
        plan.reannounce = bool(state.last_port) and port != state.last_port
        state.last_port = port
        return plan
    state.zero_runs += 1
    if state.zero_runs < ZERO_RUNS_BEFORE_RECONNECT:
        return plan
    if len(state.reconnects) >= MAX_RECONNECTS_PER_HOUR:
        plan.give_up = now - state.gave_up_at >= 3600
        if plan.give_up:
            state.gave_up_at = now
        return plan
    plan.reconnect = True
    return plan


def notify(webhook: str, title: str, text: str, ok: bool) -> None:
    payload = {
        "username": "vpn guard",
        "embeds": [{"title": title, "description": text, "color": 3066993 if ok else 15158332,
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}],
    }
    request = urllib.request.Request(webhook, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "batlab-vpnguard"})
    urllib.request.urlopen(request, timeout=15).close()


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print the decision; reconnect and post nothing")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--webhook-var", default="DISCORD_WEBHOOK_DOWNLOAD_ISSUES")
    parser.add_argument("--state-dir", default=os.path.join(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"),
                                                            "batlab-vpnguard"))
    args = parser.parse_args(argv)

    state_path = Path(args.state_dir) / "state.json"
    state = State(**json.loads(state_path.read_text())) if state_path.exists() else State()
    webhook = env_value(Path(args.env_file), args.webhook_var)
    global CONTROL_KEY
    CONTROL_KEY = env_value(Path(args.env_file), "GLUETUN_CONTROL_API_KEY")
    now = time.time()
    try:
        running = tunnel_running()
        port = forwarded_port() if running else 0
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"gluetun did not answer: {error}", file=sys.stderr)
        return 1

    plan = decide(state, running, port, now)
    print(f"tunnel {'running' if running else 'down'}, port {port}, zero runs {state.zero_runs}, "
          f"reconnects this hour {len(state.reconnects)}: "
          f"{'reconnect' if plan.reconnect else 'give up' if plan.give_up else 'reannounce' if plan.reannounce else 'nothing to do'}")
    if args.dry_run:
        return 0

    status = 0
    try:
        if plan.reconnect:
            state.reconnects.append(now)
            new_port = reconnect()
            state.zero_runs = 0
            if new_port:
                state.last_port = new_port
                reannounce()
            text = (f"The tunnel was up but had no forwarded port for {ZERO_RUNS_BEFORE_RECONNECT} checks, so it "
                    f"was reconnected ({len(state.reconnects)}/{MAX_RECONNECTS_PER_HOUR} this hour). "
                    + (f"New port {new_port}; qBittorrent was told to re-announce."
                       if new_port else f"Still no port after {PORT_WAIT_SECONDS} s; the next run tries again."))
            if webhook.startswith("http"):
                notify(webhook, "🔁 VPN reconnected for port forwarding", text, bool(new_port))
            print(text)
        elif plan.give_up and webhook.startswith("http"):
            notify(webhook, "🛑 VPN port forwarding still down",
                   f"{MAX_RECONNECTS_PER_HOUR} reconnects in the last hour got no forwarded port, so the guard "
                   "is waiting for the hour to pass. Check Proton's status, or widen GLUETUN_SERVER_COUNTRIES.", False)
        elif plan.reannounce:
            reannounce()
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
        print(f"action failed: {error}", file=sys.stderr)
        status = 1

    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state.__dict__))
    temporary.replace(state_path)
    return status


if __name__ == "__main__":
    sys.exit(main())
