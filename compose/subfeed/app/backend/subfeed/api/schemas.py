"""What the API answers, as models: FastAPI checks every answer against them
and describes them in the OpenAPI document (/api/v1/openapi.json), which the
reference (/docs) and the page's types (frontend/src/lib/schema.d.ts) are
made from. Requests' bodies are with their stores (FeedIn, SettingsPatch…)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain import LINK_KINDS
from ..links import PROVIDERS

LinkKind = Literal[LINK_KINDS]
Provider = Literal[tuple(PROVIDERS)]


class Problem(BaseModel):
    detail: str = Field(description="What went wrong, as a sentence a person can read.")


class Health(BaseModel):
    ok: bool = Field(description="subfeed answers and its database is readable.")
    version: str


# ---- feeds ----------------------------------------------------------------------------

class LastRead(BaseModel):
    at: float
    ok: bool
    message: str | None = Field(description='What it found, or why it failed ("3 new of Reddit\'s last 100 '
                                            'posts").')


class Feed(BaseModel):
    slug: str = Field(description="Its name in addresses.", examples=["sexymusicvideos"])
    title: str
    source: str = Field(description="Where its posts come from (GET /api/v1/sources).", examples=["reddit"])
    target: str = Field(description="The community it reads.", examples=["SexyMusicVideos"])
    home_url: str | None = Field(description="The community's own page.")
    enabled: bool = Field(description="Off: kept, but not read.")
    poll_minutes: int = Field(description="How often it is read.")
    created_at: float
    last: LastRead | None = Field(description="Its last read; none before the first.")
    next_at: float | None = Field(description="When it is read next; none: as soon as the poller can.")
    failures: int = Field(description="Reads that failed in a row; 0 when the last one worked.")
    changed_at: float = Field(description="When its posts or its settings last changed.")
    posts: int = Field(description="Posts it found.")
    videos: int = Field(description="Of which carry a video.")


class FeedList(BaseModel):
    feeds: list[Feed]


class SourceInfo(BaseModel):
    key: str = Field(examples=["reddit"])
    label: str
    target_label: str = Field(description="What a feed of it reads, as a form asks for it.", examples=["Subreddit"])
    target_help: str
    target_pattern: str = Field(description="A regular expression a target matches, once cleaned.")


class SourceList(BaseModel):
    sources: list[SourceInfo]


# ---- posts -----------------------------------------------------------------------------

class VideoOut(BaseModel):
    provider: Provider
    id: str = Field(examples=["fLCd0tXSLZU"])
    url: str


class LinkOut(BaseModel):
    url: str | None = Field(description="What the post links to; none for a text post.")
    kind: LinkKind = Field(description="video (it carries a video), reddit_video, image, gallery, text (no link), "
                                       "other.")
    domain: str | None = Field(examples=["youtu.be"])


class PostOut(BaseModel):
    id: str = Field(description='"<feed>:<post id>".', examples=["sexymusicvideos:1o8xk2a"])
    feed: str
    post_id: str = Field(description="The post's id on its site.")
    title: str
    url: str = Field(description="The post on its site.")
    author: str | None
    posted_at: float
    found_at: float = Field(description="When subfeed found it.")
    link: LinkOut
    videos: list[VideoOut] = Field(description="Every video it carries, in order.")


class PostPage(BaseModel):
    posts: list[PostOut]
    total: int = Field(description="Matching posts in all; posts holds one page.")
    limit: int
    offset: int


class JsonFeedAuthor(BaseModel):
    name: str


class JsonFeedItemExtension(BaseModel):
    feed: str
    post_id: str
    link_kind: LinkKind
    domain: str | None
    videos: list[VideoOut]


class JsonFeedItem(BaseModel):
    id: str
    url: str = Field(description="The post.")
    external_url: str | None = Field(None, description="What it links to.")
    title: str
    content_text: str
    date_published: str
    authors: list[JsonFeedAuthor] | None = None
    subfeed: JsonFeedItemExtension = Field(alias="_subfeed")

    model_config = ConfigDict(populate_by_name=True)


class JsonFeedExtension(BaseModel):
    about: str = Field(description="Where the _subfeed fields are described.")
    feed: str
    source: str
    target: str


class JsonFeed(BaseModel):
    """JSON Feed 1.1 (https://www.jsonfeed.org/version/1.1/), with subfeed's
    fields in "_subfeed"."""

    version: str
    title: str
    description: str
    home_page_url: str | None = None
    feed_url: str
    next_url: str | None = Field(None, description="The next page, when there is one.")
    subfeed: JsonFeedExtension = Field(alias="_subfeed")
    items: list[JsonFeedItem]

    model_config = ConfigDict(populate_by_name=True)


# ---- notifications ----------------------------------------------------------------------

class NotificationField(BaseModel):
    key: str
    label: str
    kind: Literal["text", "url", "secret", "secret_url"]
    help: str
    placeholder: str
    required: bool
    default: str | None
    secret: bool = Field(description="Write-only: answers say only whether it is set.")


class NotificationTypeOut(BaseModel):
    type: str
    label: str
    description: str
    homepage: str | None
    fields: list[NotificationField]


class EventDescription(BaseModel):
    name: str = Field(examples=["feed.failing"])
    label: str
    description: str
    default: bool = Field(description="Ticked on a new notification.")
    kind: Literal["info", "success", "warning", "failure"]


class NotificationTypes(BaseModel):
    types: list[NotificationTypeOut]
    events: list[EventDescription] = Field(description="The events to choose from.")


class LastSend(BaseModel):
    at: float
    ok: bool
    message: str


class Notification(BaseModel):
    id: str
    name: str
    type: str
    type_label: str
    enabled: bool
    events: list[str]
    config: dict[str, str | None] = Field(description="Without secrets.")
    secrets_set: list[str] = Field(description="The secret fields that have a value.")
    summary: str = Field(description="Where it sends, without secrets.")
    last: LastSend | None = Field(description="Its last send since subfeed started.")


class NotificationList(BaseModel):
    items: list[Notification]


class TestResult(BaseModel):
    ok: bool
    message: str


# ---- the system ----------------------------------------------------------------------------

class FeedCounts(BaseModel):
    total: int
    enabled: int
    failing: int = Field(description="Feeds whose last read failed.")


class SystemStatus(BaseModel):
    version: str
    started_at: float
    feeds: FeedCounts
    posts: int
    database_bytes: int
    last_round: float | None = Field(description="When the poller last went through the feeds.")
    sources: list[str]
