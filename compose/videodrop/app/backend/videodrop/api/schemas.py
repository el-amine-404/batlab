"""What the API answers, as models: FastAPI checks every answer against them
and describes them in the OpenAPI document (/api/v1/openapi.json), which the
reference (/docs) and the page's types (frontend/src/lib/schema.d.ts) are
made from. Requests' bodies are with their stores (JobIn, DestinationIn,
SettingsPatch…)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    step: str | None = Field(None, description="Which part of processing: yt-dlp's postprocessor (Merger, "
                                                "FixupM3u8, FFmpegMetadata, EmbedThumbnail…), lyrics or check.")
    step_since: float | None = Field(None, description="When this stage or step began.")
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


class FileChecks(BaseModel):
    ok: bool = Field(description="A real video of the length the site said, in the container its name says.")
    problems: list[str] = Field(description="What is wrong, or worth knowing (no sound).")
    summary: str | None = Field(description="What ffprobe read: codec, size, sound, length.",
                                examples=["AV1 1920×1080 · Opus · 3:45"])
    duration: float | None


class SongOut(BaseModel):
    artist: str
    title: str


class JobFile(BaseModel):
    index: int
    name: str = Field(examples=["Housewife (Official Video) [DPEodkHzH-s].mp4"])
    kind: FileKind
    size: int = Field(description="Bytes.")
    path: str | None = Field(description="Where it is, inside the media folder; none for a held file.",
                             examples=["music-videos/Tate McRae/Housewife (Official Video) [DPEodkHzH-s].mp4"])
    existed: bool = Field(description="The destination had it already (same name): the new copy was dropped.")
    available: bool = Field(description="It can be downloaded: it's still there.")
    checks: FileChecks | None = Field(description="Its check after the download (videos only).")
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
    notes: list[str] = Field(description="What happened beside the download: lyrics found or not.")
    song: SongOut | None = Field(description="The song its caller named, for its lyrics.")
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


# ---- reading a link -------------------------------------------------------------------

class LinkCheck(BaseModel):
    ok: bool = Field(description="videodrop takes this link. Checked without asking the site.")
    message: str | None = Field(description="Why not, as a sentence.")
    url: str
    site: str | None
    canonical: str | None = Field(description="The one spelling of this video (a YouTube link without its "
                                              "tracking).")


class ProbeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8, max_length=2000)
    destination: str | None = Field(None, max_length=40, description="As for a job: None the default, \"\" none.")
    options: OptionsPatch = Field(default_factory=OptionsPatch)


class ProbeFormat(BaseModel):
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str | None
    acodec: str | None
    video_kbps: float | None
    audio_kbps: float | None
    dynamic_range: str | None = Field(description="SDR, HDR10…")
    container: str | None
    size_bytes: int | None = Field(description="Of the file, as the site says; may be an estimate (size_exact).")
    size_exact: bool
    format_id: str | None = Field(description="yt-dlp's name for the streams picked.")


class ProbeEntry(BaseModel):
    title: str | None
    id: str | None
    duration: float | None


class ProbeTarget(BaseModel):
    destination: str | None
    path: str | None = Field(description="Where the file would be, inside the media folder; none when held.")
    exists: bool = Field(description="A file with that name is there already: the download would keep it.")


class Probe(BaseModel):
    """What a link would download, read from the site without downloading."""

    kind: Literal["video", "playlist"]
    id: str | None
    title: str | None
    uploader: str | None
    channel_url: str | None
    webpage_url: str | None = Field(description="The video's own page, as the site names it (not the link given).")
    extractor: str | None = Field(description="Which of yt-dlp's sites read it.", examples=["Youtube"])
    thumbnail: str | None
    duration: float | None = None
    upload_date: str | None = Field(None, description="YYYYMMDD.")
    views: int | None = None
    is_live: bool = False
    age_limit: int | None = None
    artist: str | None = None
    track: str | None = None
    format: ProbeFormat | None = Field(None, description="What the options pick.")
    heights: list[int] = Field(default_factory=list, description="Every video height the site has.")
    subtitles: list[str] = Field(default_factory=list, description="Languages with real subtitles.")
    automatic_captions: int = Field(0, description="Languages with the site's automatic captions.")
    automatic_has_english: bool = False
    filename: str | None = Field(None, description="Its file name, as the destination's template makes it.")
    count: int | None = Field(None, description="A playlist's videos.")
    taken: int | None = Field(None, description="Of which downloaded (playlist_limit).")
    entries: list[ProbeEntry] | None = Field(None, description="A playlist's first videos.")
    options: VideoOptions = Field(description="The options it was read with, every layer resolved.")
    target: ProbeTarget | None


# ---- issues ------------------------------------------------------------------------------

class IssueAction(BaseModel):
    label: str
    path: str = Field(description="A page of videodrop's, where it is fixed.")


class IssueOut(BaseModel):
    id: str
    level: Literal["error", "warning", "info"]
    title: str
    detail: str
    action: IssueAction | None
    since: float | None


class IssueCounts(BaseModel):
    error: int
    warning: int
    info: int


class IssueList(BaseModel):
    issues: list[IssueOut]
    counts: IssueCounts


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
