"""Notifications through Apprise (https://github.com/caronc/apprise-api):
batlab's services send a message once, with a tag naming the channel
("downloads", "download-issues"), and Apprise delivers it to whatever the
user set up for that tag: Discord, Telegram, ntfy, e-mail, and 100 more.
"""

import json
import urllib.error
import urllib.request

KINDS = ("info", "success", "warning", "failure")
MAX_BODY = 3800  # Discord's limit for one message's text, less a margin


class NotSent(RuntimeError):
    """The message did not go out; the text says why, for a log or a person."""


def send(endpoint, title, body, tag=None, kind="info", timeout=15):
    """Send one message. endpoint: Apprise's /notify/<key> address, as
    http://apprise:8000/notify/batlab. body is Markdown."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    payload = {"title": title[:250], "body": body[:MAX_BODY], "type": kind, "format": "markdown"}
    if tag:
        payload["tag"] = tag
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
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
        raise NotSent(f"Apprise answered {error.code}{f': {detail}' if detail else ''}.") from error
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        raise NotSent(f"Couldn't reach Apprise at {endpoint}: {reason}.") from error
    if status == 204:
        raise NotSent(f"Apprise has no destination for the tag {tag!r}." if tag else "Apprise has no destination.")
