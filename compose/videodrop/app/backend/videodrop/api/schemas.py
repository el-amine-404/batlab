"""What the API answers, as models: FastAPI checks every answer against them
and describes them in the OpenAPI document (/api/v1/openapi.json), which the
reference (/docs) and the page's types (frontend/src/lib/schema.d.ts) are
made from. Requests' bodies are with their stores (JobIn, DestinationIn,
SettingsPatch…)."""

from typing import Literal

from pydantic import BaseModel, Field

from ..domain import FileKind, Stage, Status
from ..options import OptionsPatch, VideoOptions


class Problem(BaseModel):
    detail: str = Field(description="What went wrong, as a sentence a person can read.")


class Health(BaseModel):
    ok: bool = Field(description="videodrop answers and its database is readable.")
    version: str


# ---- jobs --------------------------------------------------------------------------------

class Progress(BaseModel):
    stage: Stage | None = None
    percent: float | None = Field(None, description="0-100 of the whole job (a playlist's videos together); "
                                                    "none while the size isn't known.")
    downloaded_bytes: int | None = Field(None, description="Of the video downloading now.")
    total_bytes: int | None = Field(None, description="Of the video downloading now, as the site says it; may be "
                                                      "an estimate.")
    speed: float | None = Field(None, description="Bytes a second.")
    eta: float | None = Field(None, description="Seconds left for the video downloading now.")
    item: int | None = Field(None, description="Which video (1…items), for a playlist.")
    items: int | None = None
    item_title: str | None = None


class JobFile(BaseModel):
    index: int
    name: str = Field(examples=["Housewife (Official Video) [DPEodkHzH-s].mp4"])
    kind: FileKind
    size: int = Field(description="Bytes.")
    path: str | None = Field(description="Where it is, inside the media folder; none for a held file.",
                             examples=["music-videos/Tate McRae/Housewife (Official Video) [DPEodkHzH-s].mp4"])
    existed: bool = Field(description="The destination had it already (same name): the new copy was dropped.")
    available: bool = Field(description="It can be downloaded: it's still there.")
    download_url: str | None = Field(description="Downloads it (Range requests work: resume, seek). Made from the "
                                                 "address the caller reaches videodrop at.")


class CallbackLast(BaseModel):
    at: float
    event: str
    ok: bool
    message: str
    tries: int


class CallbackInfo(BaseModel):
    url: str
    events: list[str]
    signed: bool = Field(description="A secret was given: calls carry X-Videodrop-Signature.")
    last: CallbackLast | None = Field(description="How the last call went.")


class JobLinks(BaseModel):
    self: str = Field(description="GET: the job.")
    events: str = Field(description="GET: its live stream (text/event-stream), until it ends.")


class Job(BaseModel):
    id: str
    url: str
    site: str = Field(description="Downloads of one site share per_site_parallel.", examples=["youtube.com"])
    status: Status = Field(description="queued (its turn, or a retry at retry_at), running, finished, failed, "
                                       "cancelled. The last three are final.")
    stage: Stage | None = Field(description="Where a running job is.")
    destination: str | None = Field(description="Its destination; none: its files are held for its caller.")
    folder: str | None = Field(description="Its destination's folder when it was asked for.")
    client: str | None
    priority: int
    title: str | None = Field(description="Known once it starts.")
    uploader: str | None
    thumbnail: str | None
    duration: float | None = Field(description="Seconds.")
    options: VideoOptions = Field(description="What it downloads with, every layer resolved.")
    progress: Progress | None = Field(description="While it runs.")
    error: str | None = Field(description="Why it failed; for a queued job, why its last try did; for a finished "
                                          "playlist, the videos that failed.")
    attempts: int = Field(description="Tries so far.")
    retry_at: float | None = Field(description="When a queued job is tried again.")
    files: list[JobFile]
    callback: CallbackInfo | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    updated_at: float
    links: JobLinks


class JobPage(BaseModel):
    jobs: list[Job]
    total: int = Field(description="Matching jobs in all; jobs holds one page, newest first.")
    limit: int
    offset: int


class JobCounts(BaseModel):
    queued: int
    running: int
    finished: int
    failed: int
    cancelled: int


# ---- destinations ----------------------------------------------------------------------------

class Destination(BaseModel):
    slug: str = Field(examples=["music-videos"])
    name: str
    folder: str = Field(description="Inside the media folder.")
    template: str = Field(examples=["{artist}/{title} [{id}]"])
    options: OptionsPatch = Field(description="Options downloads to it take unless they set their own.")
    created_at: float
    updated_at: float


class DestinationList(BaseModel):
    destinations: list[Destination]


class TemplateFields(BaseModel):
    fields: list[str] = Field(description="What a template can hold, as {name}.")
    default: str


class FolderEntry(BaseModel):
    name: str
    path: str


class FolderListing(BaseModel):
    path: str = Field(description="Inside the media folder; empty: the media folder itself.")
    parent: str | None
    folders: list[FolderEntry]
    truncated: bool


class FolderCheck(BaseModel):
    path: str
    ok: bool = Field(description="Videos can be written there (it is made when missing).")
    exists: bool
    message: str
    free_bytes: int | None


# ---- cookies -------------------------------------------------------------------------------------

class CookieStatus(BaseModel):
    present: bool
    saved_at: float | None = None
    expires_at: float | None = Field(None, description="When the first YouTube cookie runs out.")
    expired: bool | None = None


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
    name: str = Field(examples=["job.failed"])
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
    last: LastSend | None = Field(description="Its last send since videodrop started.")


class NotificationList(BaseModel):
    items: list[Notification]


class TestResult(BaseModel):
    ok: bool
    message: str


# ---- the system ----------------------------------------------------------------------------

class DiskStatus(BaseModel):
    free_bytes: int | None
    low: bool = Field(description="Under min_free_gb: downloads wait.")
    min_free_bytes: int


class SystemStatus(BaseModel):
    version: str
    yt_dlp: str = Field(description="yt-dlp's version.")
    deno: bool = Field(description="Deno is there: yt-dlp can answer YouTube's challenge.")
    started_at: float
    jobs: JobCounts
    running: int = Field(description="Download processes now.")
    max_parallel: int
    disk: DiskStatus
    cookies: CookieStatus
    streams: int = Field(description="Live streams open.")
    database_bytes: int
    clients: list[str] = Field(description="Every client that asked for a download.")
