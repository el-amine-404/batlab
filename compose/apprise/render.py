"""Apprise's configuration from the environment, written before it starts.

Each NOTIFY_<CHANNEL> variable holds the Apprise URLs of one channel,
separated by spaces: NOTIFY_DOWNLOADS="https://discord.com/api/webhooks/…
tgram://bot/chat". Services send a message with the channel's name as its tag
("downloads", "download-issues"), and Apprise delivers it to those URLs. See
https://github.com/caronc/apprise/wiki for every service's URL.

Every sender in senders.json (lib/batlab-common) gets its own configuration,
so its own address, /notify/<sender>, with the same channels: on Discord its
messages carry its name and avatar, as they did when each posted there itself.

The files live in memory (a tmpfs) and are rewritten on every start, so the
secrets stay in compose/.env and the configuration in the repository.
"""

import json
import os
import re
import sys
import urllib.parse

CONFIG_DIR = os.environ.get("APPRISE_CONFIG_DIR", "/config")
SENDERS = os.environ.get("BATLAB_SENDERS", "/senders.json")
PREFIX = "NOTIFY_"
DISCORD = re.compile(r"^https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/(\d+)/([\w-]+)/?(?:\?(.*))?$")


def channels(environ):
    """{"download-issues": ["https://…"], …} from NOTIFY_DOWNLOAD_ISSUES and the others."""
    found = {}
    for name, value in sorted(environ.items()):
        if name.startswith(PREFIX) and value.strip():
            tag = name[len(PREFIX):].lower().replace("_", "-")
            if not re.fullmatch(r"[a-z0-9-]+", tag):
                sys.exit(f"{name}: a channel's name is letters, digits and _ only")
            found[tag] = value.split()
    return found


def as_sender(url, who, host):
    """A Discord URL that posts under the sender's name and avatar, its
    sections as plain text (Apprise's embed fields are code blocks). Other
    URLs as they are."""
    match = DISCORD.match(url)
    if match:
        base, query = f"discord://{match[1]}/{match[2]}/", match[3] or ""
    elif url.startswith("discord://"):
        base, _, query = url.partition("?")
    else:
        return url
    params = dict(urllib.parse.parse_qsl(query))
    params.setdefault("botname", who["name"].replace("{host}", host))
    params.setdefault("fields", "no")
    if who.get("avatar"):
        params.setdefault("avatar_url", who["avatar"])
    else:
        params.setdefault("avatar", "no")  # Discord's own, not Apprise's logo
    if who.get("link"):
        params.setdefault("href", who["link"])
    return base + "?" + urllib.parse.urlencode(params)


def main():
    found = channels(os.environ)
    with open(SENDERS, encoding="utf-8") as file:
        senders = {key: who for key, who in json.load(file).items() if not key.startswith("_")}
    host = os.environ.get("BATLAB_HOST") or "batlab"
    for key, who in senders.items():
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", key):
            sys.exit(f"senders.json: {key!r} isn't a usable address")
        # JSON is YAML: no quoting to get wrong in a URL.
        urls = [{as_sender(url, who, host): {"tag": tag}} for tag, addresses in found.items() for url in addresses]
        with open(os.path.join(CONFIG_DIR, f"{key}.yml"), "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump({"version": 1, "urls": urls}, out)
    count = sum(len(addresses) for addresses in found.values())
    print(f"apprise: {count} destination(s) for {', '.join(found) or 'no channel'}; "
          f"senders: {', '.join(senders)}", flush=True)


if __name__ == "__main__":
    main()
