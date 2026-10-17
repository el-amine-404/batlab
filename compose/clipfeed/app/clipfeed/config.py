"""What the environment says (compose/clipfeed/docker-compose.yml). The feeds
it names are made on the first start only; after that the API changes them."""

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    port: int
    state_dir: Path
    # "slug=reddit:Subreddit,slug2=reddit:Other": the first feeds.
    seed: list[tuple[str, str, str]]
    # Reddit asks for a descriptive one; a generic one is refused sooner.
    user_agent: str

    @property
    def db_path(self) -> Path:
        return self.state_dir / "clipfeed.db"


def _seed(raw: str) -> list[tuple[str, str, str]]:
    feeds = []
    for part in filter(None, (p.strip() for p in raw.split(","))):
        found = re.fullmatch(r"([a-z0-9-]{1,40})=(reddit):([A-Za-z0-9_]{2,21})", part)
        if not found:
            raise SystemExit(f"CLIPFEED_FEEDS: {part!r} isn't slug=reddit:Subreddit")
        feeds.append(found.groups())
    return feeds


def load() -> Config:
    port = os.environ.get("CLIPFEED_PORT", "8830").strip()
    if not port.isdigit():
        raise SystemExit(f"CLIPFEED_PORT must be a port number, not {port!r}")
    return Config(
        port=int(port),
        state_dir=Path(os.environ.get("CLIPFEED_STATE", "/state")),
        seed=_seed(os.environ.get("CLIPFEED_FEEDS", "")),
        user_agent=os.environ.get("CLIPFEED_USER_AGENT", "").strip()
        or "linux:batlab-clipfeed:1 (a home server's music video feed)",
    )
