"""What the environment says (compose/subfeed/docker-compose.yml). The feeds
and the notification it names are made on the first start only; after that
the API, and the page, own them."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = "linux:batlab-subfeed:1 (a home server's subreddit feeds)"
_URL = re.compile(r"^https?://[^\s/]+(?:/\S*)?$")


@dataclass(frozen=True)
class Seed:
    """A feed to make on the first start."""

    slug: str
    source: str
    target: str


@dataclass(frozen=True)
class Config:
    port: int
    # The database: feeds, their posts, settings and notifications.
    state_dir: Path
    # The page, built (the image's static folder).
    static_dir: Path
    seed: tuple[Seed, ...]
    # Reddit asks for a descriptive one; a generic one is refused sooner.
    user_agent: str
    # The page's address, for links in messages; a setting after the first start.
    public_url: str
    # The first notification (Apprise), when given.
    apprise_url: str
    apprise_tag: str

    @property
    def database(self) -> Path:
        return self.state_dir / "subfeed.db"


def _seed(raw: str) -> tuple[Seed, ...]:
    """ "slug=reddit:Subreddit,other=reddit:Other"."""
    feeds = []
    for part in filter(None, (p.strip() for p in raw.split(","))):
        found = re.fullmatch(r"([a-z0-9-]{1,40})=([a-z]+):([A-Za-z0-9_]{2,21})", part)
        if not found:
            raise SystemExit(f"SUBFEED_FEEDS: {part!r} isn't slug=reddit:Subreddit")
        feeds.append(Seed(*found.groups()))
    return tuple(feeds)


def _url(name: str, value: str) -> str:
    value = value.strip().rstrip("/")
    if value and not _URL.match(value):
        raise SystemExit(f"{name} must start with http:// or https://, not {value!r}")
    return value


def load(env: Mapping[str, str] = os.environ) -> Config:
    port = env.get("SUBFEED_PORT", "8830").strip()
    if not port.isdigit():
        raise SystemExit(f"SUBFEED_PORT must be a port number, not {port!r}")
    return Config(
        port=int(port),
        state_dir=Path(env.get("SUBFEED_STATE", "/state")),
        static_dir=Path(env.get("SUBFEED_STATIC", "/opt/subfeed/static")),
        seed=_seed(env.get("SUBFEED_FEEDS", "")),
        user_agent=env.get("SUBFEED_USER_AGENT", "").strip() or DEFAULT_USER_AGENT,
        public_url=_url("SUBFEED_PUBLIC_URL", env.get("SUBFEED_PUBLIC_URL", "")),
        apprise_url=_url("SUBFEED_APPRISE_URL", env.get("SUBFEED_APPRISE_URL", "")),
        apprise_tag=env.get("SUBFEED_APPRISE_TAG", "").strip(),
    )
