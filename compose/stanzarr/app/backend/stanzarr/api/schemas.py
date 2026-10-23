"""What the API answers, as the reference (/docs) shows it."""

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["synced", "ai_corrected", "ai_checked", "ai_unchecked", "plain", "instrumental", "missing"]


class Problem(BaseModel):
    detail: str = Field(description="What went wrong, as a sentence a person can read.")


class Health(BaseModel):
    ok: bool
    version: str


class Ok(BaseModel):
    ok: bool = True


# ---- the library ------------------------------------------------------------------------

class Language(BaseModel):
    code: str
    name: str


class SongRow(BaseModel):
    id: int
    artist: str
    album: str
    title: str
    duration: float
    rel: str = Field(description="Where it is, under its library folder.")
    status: Status
    formats: list[str] = Field(description="The lyrics files it has: lrc, txt, embedded…")
    made_by: list[str] = Field(description="Who made them: person, ai, lrclib, netease, musixmatch, genius, unknown.")
    added_at: int
    languages: list[Language]
    to_check: int | None = Field(None, description="AI lines still to check.")
    job: str | None = Field(None, description="Its latest AI job's status.")


class SongList(BaseModel):
    total: int
    page: int
    size: int
    songs: list[SongRow]


class LyricsFile(BaseModel):
    id: int
    path: str
    name: str
    format: str
    synced: bool
    language: str
    language_name: str
    source: str = Field(description="lrclib, ai, person or unknown (what the file says of itself).")
    ai_state: str | None
    lines: int
    unsure: int
    checked: int


class Coverage(BaseModel):
    synced: int = 0
    ai_corrected: int = 0
    ai_checked: int = 0
    ai_unchecked: int = 0
    plain: int = 0
    instrumental: int = 0
    missing: int = 0


class ToCheck(BaseModel):
    songs: int
    lines: int


class SystemStatus(BaseModel):
    """What Homepage's card and the Status screen read."""

    version: str
    started_at: float
    songs: int
    by_status: Coverage
    to_check: ToCheck
    background: str = Field(description="What it is doing now; empty when idle.")
    last_scan: dict
    workers_online: int
    gpu_online: bool
    sources: list[str] = Field(description="The sources asked, in order.")
    read_only: bool
    database_bytes: int


# ---- the lyrics, for other apps -----------------------------------------------------------

class LyricsAnswer(BaseModel):
    found: bool
    synced: bool = Field(description="True: lrc holds them with timings.")
    instrumental: bool = False
    lrc: str | None = Field(None, description="Synced lyrics, as LRC.")
    text: str | None = Field(None, description="The words alone, one line each.")
    origin: str | None = Field(None, description="person, ai or the source's name: who made the timings.")
    from_: Literal["library", "source", "none"] = Field(alias="from", description="Where the answer came from.")
    song_id: int | None = Field(None, description="The library's song, when it was one.")
    source: str | None = Field(None, description="The source that gave it, when one did.")

    model_config = {"populate_by_name": True}


# ---- the queue and the workers ------------------------------------------------------------

class Job(BaseModel):
    id: int
    song_id: int
    artist: str
    title: str
    kind: str
    status: str
    stage: str
    worker: str
    error: str
    created_at: int
    started_at: int | None
    finished_at: int | None


class Worker(BaseModel):
    id: str | None = Field(description="Its key's id; None for a worker on the shared key (before 1.1).")
    name: str
    prefix: str | None = Field(description="The key's first letters, to tell keys apart.")
    created_at: float | None
    last_used: float | None
    shared_key: bool = Field(description="It uses the key all workers shared before 1.1.")
    gpu: str = Field(description="The GPU it reported, as the driver names it; empty: none.")
    vram: int = Field(description="Its GPU's memory, MB.")
    version: str = Field(description="Its stanzarr-worker.")
    last_seen: int | None
    online: bool


class WorkerList(BaseModel):
    workers: list[Worker]
    shared_key: bool = Field(description="The key all workers shared before 1.1 still works.")
    shared_key_fixed: bool = Field(description="It is set by STANZARR_WORKER_KEY: retired by removing it there.")


class WorkerMade(BaseModel):
    key: str = Field(description="Its key: shown this once, kept only as a fingerprint.")
    worker: Worker


# ---- integrations (sources, notifications) ------------------------------------------------

class TestResult(BaseModel):
    ok: bool
    message: str


class Order(BaseModel):
    ids: list[str] = Field(min_length=1, description="Every item's id once, first to be asked first.")


class FormField(BaseModel):
    key: str
    label: str
    kind: Literal["text", "url", "secret", "secret_url"]
    help: str
    placeholder: str
    required: bool
    default: str | None
    secret: bool = Field(description="Write-only: answers say only whether it is set.")


class LastSend(BaseModel):
    at: float
    ok: bool
    message: str


class NotificationTypeOut(BaseModel):
    type: str
    label: str
    description: str
    homepage: str | None
    fields: list[FormField]


class EventDescription(BaseModel):
    name: str = Field(examples=["ai.synced"])
    label: str
    description: str
    default: bool = Field(description="Ticked on a new notification.")
    kind: Literal["info", "success", "warning", "failure"]


class NotificationTypes(BaseModel):
    types: list[NotificationTypeOut]
    events: list[EventDescription] = Field(description="The events to choose from.")


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
    last: LastSend | None = Field(description="Its last send since Stanzarr started.")


class NotificationList(BaseModel):
    items: list[Notification]


class SourceTypeOut(NotificationTypeOut):
    synced: bool = Field(description="True: it can give synced lyrics, not only plain text.")


class SourceTypes(BaseModel):
    types: list[SourceTypeOut]


class Source(BaseModel):
    id: str
    name: str
    type: str
    type_label: str
    enabled: bool
    config: dict[str, str | None] = Field(description="Without secrets.")
    secrets_set: list[str]
    last: LastSend | None = Field(description="Its last answer: to a search or a test.")
    found_songs: int = Field(0, description="Songs with a lyrics file it made.")
    last_found: float | None = Field(None, description="When it last found lyrics for a song.")


class SourceList(BaseModel):
    items: list[Source]
