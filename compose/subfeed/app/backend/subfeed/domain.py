"""What subfeed deals in, whatever the source: a post, and the videos it
carries. Pure data: sources make them (sources/), the stores keep them."""

from dataclasses import dataclass, field

# What a post links to (Post.kind), for readers to filter on: video when it
# carries a video of a known provider (links.PROVIDERS), else its link's kind.
LINK_KINDS = ("video", "reddit_video", "image", "gallery", "text", "other")


class NotFound(LookupError):
    """The message is shown as is."""


class Conflict(ValueError):
    """The message is shown as is."""


class Invalid(ValueError):
    """The message is shown as is."""


@dataclass(frozen=True)
class Video:
    provider: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.id}"


@dataclass
class Post:
    id: str
    title: str
    # The post itself, on its site.
    url: str
    author: str | None
    posted_at: float
    # What it links to; None for a text post.
    link: str | None = None
    videos: list[Video] = field(default_factory=list)
