"""Notifications through Apprise (https://github.com/caronc/apprise-api):
batlab's services send a message once, with a tag naming the channel
("downloads", "download-issues"), and Apprise delivers it to whatever the
user set up for that tag: Discord, Telegram, ntfy, e-mail, and 100 more.

Each sender has its own address, /notify/<sender> (senders.json), so Discord
shows who sent it. Scripts on the host send with deliver(), or from a shell:

    python3 -I lib/batlab-common/batlab_common/notify.py updater updates success \\
        "Updated jellyfin" --field Version "10.11.0"

and with BATLAB_NOTIFY_FALLBACK set to a Discord webhook, a message still goes
out when Apprise is down: an alert must not depend on the thing that broke.
Standard library only, and no import from the package, so that a shell can
run this file on its own.
"""

import argparse
import datetime
import json
import os
import socket
import sys
import urllib.error
import urllib.request

KINDS = ("info", "success", "warning", "failure")
MAX_BODY = 3800  # Discord's limit for one message's text, less a margin
# Apprise from the host (compose/apprise publishes it on the loopback).
HOST_BASE = "http://127.0.0.1:8011/notify"
SENDERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "senders.json")
# Apprise's colours for each kind, so a card sent straight to Discord when
# Apprise is down looks like one sent through it.
COLOURS = {"info": 0x3AA3E3, "success": 0x3AA337, "warning": 0xCACF29, "failure": 0xA32037}


class NotSent(RuntimeError):
    """The message did not go out; the text says why, for a log or a person.
    reached is False when Apprise itself could not be reached."""

    def __init__(self, message, reached=True):
        super().__init__(message)
        self.reached = reached


def send(endpoint, title, body, tag=None, kind="info", timeout=15, attach=()):
    """Send one message. endpoint: Apprise's /notify/<key> address, as
    http://apprise:8000/notify/musicguard. body is Markdown. attach: (filename,
    bytes) pairs, as a picture under the message."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    payload = {"title": title[:250], "body": body[:MAX_BODY], "type": kind, "format": "markdown"}
    if tag:
        payload["tag"] = tag
    if attach:
        # Files as a form, not base64 in JSON: Django caps a JSON body at
        # 2.5 MB (Apprise 2.0's APPRISE_UPLOAD_MAX_MEMORY_SIZE does not reach
        # it), and a clip is more ("JSON Payload provided is too large").
        data, kind_of = form(payload, attach)
    else:
        data, kind_of = json.dumps(payload).encode(), "application/json"
    request = urllib.request.Request(endpoint, data=data, method="POST",
                                     headers={"Content-Type": kind_of, "Accept": "application/json",
                                              "User-Agent": "batlab"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(64 * 1024)
            status = response.status
    except urllib.error.HTTPError as error:
        detail = error.read(8192).decode(errors="replace").strip()
        tried = None
        try:
            answer = json.loads(detail)
            detail, tried = answer.get("error") or detail, answer.get("details")
        except (ValueError, AttributeError):
            pass
        if error.code == 424 and tried == []:
            # Apprise 2 answers 424 with nothing tried when no URL has the tag.
            raise NotSent(f"Apprise has no destination for the tag {tag!r}." if tag else "Apprise has no destination.")
        if error.code == 424:
            raise NotSent(f"Apprise couldn't deliver it to every destination{f': {detail}' if detail else ''}.")
        raise NotSent(f"Apprise answered {error.code}{f': {detail}' if detail else ''}.",
                      reached=error.code < 500) from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise NotSent(f"Couldn't reach Apprise at {endpoint}: {reason}.", reached=False) from error
    if status == 204:
        raise NotSent(f"Apprise has no destination for the tag {tag!r}." if tag else "Apprise has no destination.")


def form(fields, files):
    """(body, content type) of a multipart/form-data request: fields as text,
    files ((filename, bytes) pairs) each as an "attachment"."""
    boundary = f"batlab-{os.urandom(12).hex()}"
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                     f"Content-Type: text/plain; charset=utf-8\r\n\r\n".encode()
                     + str(value).encode() + b"\r\n")
    for filename, data in files:
        safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="attachment"; filename="{safe}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def sender(key, host=None):
    """{"name", "avatar", "link"} of a sender in senders.json, {host} filled
    in; a sender not listed is named by its key."""
    with open(SENDERS, encoding="utf-8") as file:
        found = dict(json.load(file).get(key) or {"name": key})
    found["name"] = found["name"].replace("{host}", host or socket.gethostname())
    return found


def fields_text(fields):
    """Markdown for (name, value) pairs, one line each: "**Name**  value". A
    value of several lines starts on the line under its name.

    The card's stripe already says how it went, so a title is what happened in
    plain words ("Data disk unavailable", "Updated jellyfin"): no emoji, and
    nothing a field repeats."""
    lines = []
    for name, value in fields:
        value = (value or "-").strip()[:1000]
        lines.append(f"**{name}**\n{value}" if "\n" in value else f"**{name}**  {value}")
    return "\n".join(lines)


def discord(webhook, key, title, body, kind="info", timeout=15):
    """Post a card straight to a Discord webhook, as sender key would through
    Apprise. NotSent when Discord does not take it."""
    who = sender(key)
    card = {"title": title[:250], "description": body[:MAX_BODY], "color": COLOURS[kind],
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if who.get("link"):
        card["url"] = who["link"]
    payload = {"username": who["name"][:80], "embeds": [card], "allowed_mentions": {"parse": []}}
    if who.get("avatar"):
        payload["avatar_url"] = who["avatar"]
    request = urllib.request.Request(webhook, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "batlab"})
    try:
        urllib.request.urlopen(request, timeout=timeout).close()
    except (urllib.error.URLError, OSError) as error:
        raise NotSent(f"Discord didn't take it either: {getattr(error, 'reason', error)}.") from error


def deliver(key, tag, title, body, kind="info", fallback=None, base=None, timeout=15):
    """Send as sender key through Apprise at base (HOST_BASE, or
    BATLAB_NOTIFY_BASE); when Apprise can't be reached and fallback is a
    Discord webhook, straight to it. Returns "apprise" or "discord"; NotSent
    when neither took it."""
    base = (base or os.environ.get("BATLAB_NOTIFY_BASE") or HOST_BASE).rstrip("/")
    try:
        send(f"{base}/{key}", title, body, tag=tag, kind=kind, timeout=timeout)
        return "apprise"
    except NotSent as error:
        if error.reached or not fallback:
            raise
        discord(fallback, key, title, body, kind, timeout)
        return "discord"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send one message through Apprise as one of batlab's senders.")
    parser.add_argument("sender", help="a key of senders.json: updater, watchdog…")
    parser.add_argument("tag", help="the channel: alerts, updates, download-issues…")
    parser.add_argument("kind", choices=KINDS)
    parser.add_argument("title")
    parser.add_argument("--body", default="", help="Markdown, before the fields")
    parser.add_argument("--field", nargs=2, action="append", default=[], metavar=("NAME", "VALUE"))
    args = parser.parse_args(argv)
    body = "\n\n".join(part for part in (args.body, fields_text(args.field)) if part)
    try:
        how = deliver(args.sender, args.tag, args.title, body or args.title, args.kind,
                      fallback=os.environ.get("BATLAB_NOTIFY_FALLBACK", "").strip() or None)
    except NotSent as error:
        print(f"notify: {error}", file=sys.stderr)
        return 1
    if how == "discord":
        print("notify: Apprise is unreachable; sent straight to Discord", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
