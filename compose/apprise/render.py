"""Apprise's configuration from the environment, written before it starts.

Each NOTIFY_<CHANNEL> variable holds the Apprise URLs of one channel,
separated by spaces: NOTIFY_DOWNLOADS="https://discord.com/api/webhooks/…
tgram://bot/chat". Services send a message with the channel's name as its tag
("downloads", "download-issues"), and Apprise delivers it to those URLs. See
https://github.com/caronc/apprise/wiki for every service's URL.

Every sender in senders.json (lib/batlab-common) gets its own configuration,
so its own address, /notify/<sender>, with the same channels: on Discord its
messages carry its name and avatar, as they did when each posted there itself.

On Discord a message is one card (template.json per sender): the title, the
body under it, and a stripe in the message's colour (success, warning,
failure, info). Nothing else: Apprise's own card puts an "Apprise" author line
over every title, and without format=markdown it sends plain text, no card.

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


def card(who):
    """The Discord message template of a sender: one embed, its title linking
    to the sender's page when it has one. {{…}} are Apprise's tokens, filled in
    JSON-escaped; app_color_int is the type's colour as Discord wants it."""
    embed = {"title": "{{app_title}}", "description": "{{app_body}}", "color": "@COLOR@"}
    if who.get("link"):
        embed["url"] = who["link"]
    text = json.dumps({"embeds": [embed], "allowed_mentions": {"parse": []}})
    # A number in the template, not a string: Discord refuses "color": "123".
    return text.replace('"@COLOR@"', "{{app_color_int}}")


def as_sender(url, who, host, template):
    """A Discord URL that posts under the sender's name and avatar, as the
    card in template. Other URLs as they are."""
    match = DISCORD.match(url)
    if match:
        base, query = f"discord://{match[1]}/{match[2]}/", match[3] or ""
    elif url.startswith("discord://"):
        base, _, query = url.partition("?")
    else:
        return url
    params = dict(urllib.parse.parse_qsl(query))
    params.setdefault("botname", who["name"].replace("{host}", host))
    params.setdefault("template", template)
    if who.get("avatar"):
        params.setdefault("avatar_url", who["avatar"])
    else:
        params.setdefault("avatar", "no")  # Discord's own, not Apprise's logo
    # quote, not urlencode's quote_plus: Apprise reads a "+" as a "+", and the
    # name came out as "watchdog+on+lab2".
    return base + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def main():
    found = channels(os.environ)
    with open(SENDERS, encoding="utf-8") as file:
        senders = {key: who for key, who in json.load(file).items() if not key.startswith("_")}
    host = os.environ.get("BATLAB_HOST") or "batlab"
    for key, who in senders.items():
        if not re.fullmatch(r"[a-z0-9_-]{1,64}", key):
            sys.exit(f"senders.json: {key!r} isn't a usable address")
        template = os.path.join(CONFIG_DIR, f"{key}.discord.json")
        with open(template, "w", encoding="utf-8") as out:
            out.write(card(who))
        # JSON is YAML: no quoting to get wrong in a URL.
        urls = [{as_sender(url, who, host, template): {"tag": tag}}
                for tag, addresses in found.items() for url in addresses]
        with open(os.path.join(CONFIG_DIR, f"{key}.yml"), "w", encoding="utf-8") as out:
            os.fchmod(out.fileno(), 0o600)
            json.dump({"version": 1, "urls": urls}, out)
    count = sum(len(addresses) for addresses in found.values())
    print(f"apprise: {count} destination(s) for {', '.join(found) or 'no channel'}; "
          f"senders: {', '.join(senders)}", flush=True)


if __name__ == "__main__":
    main()
