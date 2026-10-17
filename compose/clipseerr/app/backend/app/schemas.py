"""What the API answers, as models: they make the OpenAPI description
(/api/v1/openapi.json) exact, which the page's TypeScript types
(frontend/src/lib/schema.d.ts) and the API reference (/api/docs) are made
from. Change a model here, then run `npm run gen:api` in frontend/."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .catalog.models import Shelf
from .settings import Settings

Kind = Literal["audio", "video"]
RequestStatus = Literal["queued", "sent", "downloading", "completed", "failed", "cancelled"]
AttemptState = Literal["sent", "downloading", "completed", "failed"]


class Health(BaseModel):
    ok: bool


class Problem(BaseModel):
    detail: str = Field(description="What went wrong, as a sentence a person can read.")


class Tag(BaseModel):
    id: str = Field(description='"explicit", "fan-upload", "apple", "soon", "in-library", "lang:<code>", "genre:<name>".')
    label: str
    hint: str = Field(description="What it means, in one line.")
    count: int = 0


class Tags(BaseModel):
    tags: list[Tag]


class LibraryFile(BaseModel):
    kind: Literal["audio", "video"] = Field(description="The song, or the music video file.")
    name: str = Field(description="The file's name, as the download gives it.")
    size: int | None = Field(None, description="Bytes, when the library says.")
    url: str = Field(description="Where to download it: valid while the library still holds the file.")


class LibraryFiles(BaseModel):
    files: list[LibraryFile]


class Views(BaseModel):
    views: dict[str, str] = Field(description='YouTube video id → its views, as YouTube writes them: "1.2M".')
    released: dict[str, str] = Field(default_factory=dict, description="YouTube video id → the day it came out.")


class Home(BaseModel):
    shelves: list[Shelf]


# ---- requests ---------------------------------------------------------------------------

class Media(BaseModel):
    video_key: str = Field(description="The catalog's video id (yt:…, am:…).")
    video_id: str | None = Field(description="The YouTube video.")
    audio_id: str | None = Field(description="The song's official audio on YouTube Music, when one matched.")
    title: str
    artist: str
    album: str | None
    year: str | None
    duration: int | None
    thumbnail: str | None
    video_title: str | None
    artist_id: str | None
    links: dict[str, str]


class Attempt(BaseModel):
    service_id: str
    service_name: str
    service_type: str
    round: int = Field(description="Each retry starts a new round.")
    external_id: str | None = Field(description="The service's own id for it.")
    state: AttemptState
    message: str | None
    quality: str | None
    progress: float | None = Field(description="0 to 1, while downloading, when the service says.")
    started_at: float
    updated_at: float


class Request(BaseModel):
    id: str
    kind: Kind
    status: RequestStatus
    media: Media
    round: int
    attempts: list[Attempt]
    message: str | None
    quality: str | None = Field(description='What it was got as: "FLAC", "OPUS", "MP4 best".')
    created_at: float
    updated_at: float
    finished_at: float | None


class RequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_id: str = Field(description="The catalog's video id: yt:<YouTube id> or am:<Apple Music id>.",
                          examples=["yt:TUVcZfQe-Kw"])
    kind: Kind = Field(description="audio: the song, at the best quality a service gets; video: the video file.")


class RequestCounts(BaseModel):
    active: int
    completed: int
    failed: int
    all: int


class RequestList(BaseModel):
    requests: list[Request]
    total: int = Field(description="Matching requests in all; requests holds one page.")
    counts: RequestCounts


class ReportIn(BaseModel):
    """What an app (a Webhook service that reports back) says about its attempt."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["downloading", "completed", "failed"]
    message: str | None = Field(None, max_length=500)
    quality: str | None = Field(None, max_length=60)
    progress: float | None = Field(None, ge=0, le=1)


class VideoRequests(BaseModel):
    """The requests for one video, by kind: the newest of each."""

    audio: Request | None
    video: Request | None


# ---- integrations (services and notifications) -------------------------------------------

class FieldChoice(BaseModel):
    value: str
    label: str


class IntegrationField(BaseModel):
    key: str
    label: str
    kind: Literal["text", "url", "secret", "secret_url", "select", "number", "switch"]
    help: str
    placeholder: str
    required: bool
    default: str | int | bool | None
    choices: list[FieldChoice]
    minimum: int | None
    maximum: int | None
    secret: bool


class IntegrationType(BaseModel):
    type: str
    label: str
    description: str
    homepage: str | None
    fields: list[IntegrationField]


class LastUse(BaseModel):
    at: float
    ok: bool
    message: str


class Integration(BaseModel):
    id: str
    name: str
    type: str
    type_label: str
    enabled: bool
    position: int
    events: list[str] = Field(description="Notifications: the events it gets.")
    config: dict[str, str | int | bool | None] = Field(description="Without secrets.")
    secrets_set: list[str] = Field(description="The secret fields that have a value.")
    summary: str
    last: LastUse | None = Field(description="Its last use since clipseerr started.")
    kinds: list[Kind] | None = Field(None, description="Services: what they take.")


class IntegrationList(BaseModel):
    items: list[Integration]


class EventDescription(BaseModel):
    name: str
    label: str
    description: str


class IntegrationTypes(BaseModel):
    types: list[IntegrationType]
    events: list[EventDescription] = Field(description="Notifications: the events to choose from.")


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(description="Every service's id once, first to be asked first.")


class TestResult(BaseModel):
    ok: bool
    message: str


class Chain(BaseModel):
    audio: list[str] = Field(description="The services audio requests go to, in order (names).")
    video: list[str]


# ---- settings and the system ------------------------------------------------------------------

class Option(BaseModel):
    value: str
    label: str


class SettingsOut(Settings):
    regions: list[Option]
    storefronts: list[Option]


class SourceHealth(BaseModel):
    ok: bool
    at: float
    error: str | None
    ms: int | None = None


class SystemStatus(BaseModel):
    version: str
    ytmusicapi: str
    started_at: float
    cache: dict[str, int]
    sources: dict[str, SourceHealth]
    requests: RequestCounts
    chain: Chain
    last_round: float | None
