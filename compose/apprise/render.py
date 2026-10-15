"""Apprise's configuration from the environment, written before it starts.

Each NOTIFY_<CHANNEL> variable holds the Apprise URLs of one channel,
separated by spaces: NOTIFY_DOWNLOADS="https://discord.com/api/webhooks/…
tgram://bot/chat". Services send a message with the channel's name as its tag
("downloads", "download-issues"), and Apprise delivers it to those URLs. See
https://github.com/caronc/apprise/wiki for every service's URL.

The file lives in memory (a tmpfs) and is rewritten on every start, so the
secrets stay in compose/.env and the configuration in the repository.
"""

import json
import os
import re
import sys

CONFIG = os.path.join(os.environ.get("APPRISE_CONFIG_DIR", "/config"), "batlab.yml")
PREFIX = "NOTIFY_"


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


def main():
    found = channels(os.environ)
    # JSON is YAML: no quoting to get wrong in a URL.
    urls = [{url: {"tag": tag}} for tag, addresses in found.items() for url in addresses]
    with open(CONFIG, "w", encoding="utf-8") as out:
        os.fchmod(out.fileno(), 0o600)
        json.dump({"version": 1, "urls": urls}, out)
    print(f"apprise: {len(urls)} destination(s) for {', '.join(found) or 'no channel'}", flush=True)


if __name__ == "__main__":
    main()
