"""What the API answers, as models: they make the OpenAPI description
(/api/v1/openapi.json) exact, which the docs' API reference and the page's
TypeScript types (frontend/src/lib/schema.d.ts) are generated from.

Change a model here, then run `npm run gen:api` in frontend/.
"""

from typing import Literal

from pydantic import BaseModel, Field

Engine = Literal["spotdl", "ytdlp"]
# "finished" as the download contract says (docs/services.md); "done" before 1.8.
JobStatus = Literal["queued", "running", "finished", "partial", "failed", "cancelled"]
RetryState = Literal["waiting", "queued", "retrying", "gave_up", "failed"]


class Health(BaseModel):
    ok: bool
    version: str = Field(description="songdrop's release.")


class Problem(BaseModel):
    detail: str = Field(description="What went wrong, as a sentence a person can read.")


# ---- links ---------------------------------------------------------------------------

class Detection(BaseModel):
    supported: bool
    message: str | None = Field(None, description="Why the link can't be downloaded; only when not supported.")
    engine: Engine | None = None
    engine_label: str | None = None
    site: str | None = None
    kind: str | None = Field(None, description="track, album, playlist, artist (Spotify); media or playlist.")
    url: str | None = None
    has_playlist: bool | None = Field(None, description="A video link that also names a playlist.")


class Choice(BaseModel):
    value: str
    label: str


class OptionField(BaseModel):
    key: str
    type: Literal["select", "switch", "time"]
    flag: str = Field(description="The engine's command-line flag this stands for.")
    label: str
    help: str
    choices: list[Choice] | None = None
    default: str | bool = Field(description="Where the option starts: the saved default, else builtin.")
    builtin: str | bool
    kinds: list[str] | None = Field(None, description="Only for links of these kinds.")
    needs: Literal["has_playlist", "list"] | None = None


class EngineOptions(BaseModel):
    label: str
    fields: list[OptionField]


class OptionsSpec(BaseModel):
    engines: dict[Engine, EngineOptions]


# ---- jobs -----------------------------------------------------------------------------

class JobFile(BaseModel):
    id: str
    name: str
    size: int
    title: str | None
    artist: str | None
    delivered: bool = Field(description="Copied to the output folder.")
    url: str = Field(description="Where to download it.")


class MissingSong(BaseModel):
    """A song of the job that is missing (the job's errors)."""

    index: int | None
    name: str
    message: str
    url: str | None = Field(description="The song's own link; without one it can't be tried alone.")
    state: RetryState
    tries: int
    max_tries: int
    next_retry_at: float | None


class JobNote(BaseModel):
    name: str
    message: str


class AskedBy(BaseModel):
    kind: Literal["user", "key"]
    id: str
    name: str


class Job(BaseModel):
    id: str
    url: str
    engine: Engine
    site: str
    kind: str
    status: JobStatus
    title: str | None
    message: str | None
    total: int
    completed: int
    progress: float | None = Field(description="0 to 1, while running.")
    current: str | None = Field(description="The song downloading now.")
    created_at: float
    started_at: float | None
    finished_at: float | None
    expires_at: float | None = Field(description="When the job and its files leave songdrop.")
    files: list[JobFile]
    errors: list[MissingSong]
    notes: list[JobNote]
    archive_url: str | None
    asked_by: AskedBy | None = Field(None, description="Who asked: a person signed in, or an app's API key, named as "
                                                       "they were then. None before songdrop had accounts.")


class JobList(BaseModel):
    jobs: list[Job]
    retention_hours: int


# ---- settings --------------------------------------------------------------------------

class Settings(BaseModel):
    deliver: bool = Field(description="Copy finished songs to the output folder.")
    output_root: str
    output_folder: str = Field(description="Inside output_root.")
    output_folder_exists: bool
    retention_hours: int
    max_items: int
    concurrent_jobs: int
    spotdl_parallel: int
    job_timeout_minutes: int
    retry_minutes: list[int] = Field(description="Minutes before each later try of a failed song.")
    public_url: str = Field(description="The page's address, for links in event messages.")
    defaults: dict[Engine, dict[str, str | bool]]
    limits: dict[str, list[int] | int] = Field(description="The allowed range of each number.")


class CookieStatus(BaseModel):
    present: bool
    saved_at: float | None = None
    expires_at: float | None = Field(None, description="When the first YouTube cookie runs out.")
    expired: bool | None = None


# ---- folders ----------------------------------------------------------------------------

class Folder(BaseModel):
    name: str
    path: str


class FolderListing(BaseModel):
    root: str
    path: str = Field(description="Relative to root; empty is root itself.")
    parent: str | None
    folders: list[Folder]
    truncated: bool


class FolderCheck(BaseModel):
    path: str
    ok: bool = Field(description="Songs can be written there (now, or once songdrop makes it).")
    exists: bool
    message: str
    free_bytes: int | None


# ---- connections ---------------------------------------------------------------------------

class EventType(BaseModel):
    name: str
    label: str
    description: str
    default: bool = Field(description="Ticked when a new connection is made.")


class ConnectionField(BaseModel):
    key: str
    label: str
    kind: Literal["text", "url", "secret_url"]
    secret: bool = Field(description="Write-only: never sent back, only listed in secrets_set.")
    help: str
    placeholder: str
    required: bool


class ConnectionType(BaseModel):
    type: str
    label: str
    description: str
    fields: list[ConnectionField]


class ConnectionTypes(BaseModel):
    types: list[ConnectionType]
    events: list[EventType]


class LastSend(BaseModel):
    at: float
    event: str
    ok: bool
    error: str | None


class Connection(BaseModel):
    id: str
    name: str
    type: str
    type_label: str
    enabled: bool
    events: list[str]
    config: dict[str, str] = Field(description="The type's fields, secrets left out.")
    secrets_set: list[str]
    summary: str = Field(description="Where it sends, without the secret.")
    last: LastSend | None = Field(description="The last send since songdrop started.")


class ConnectionList(BaseModel):
    connections: list[Connection]


class TestResult(BaseModel):
    ok: bool
    message: str


# ---- system ---------------------------------------------------------------------------------

class Tool(BaseModel):
    name: str
    version: str | None
    role: str


class Disk(BaseModel):
    label: str
    path: str
    total_bytes: int | None
    free_bytes: int | None


class JobCounts(BaseModel):
    by_status: dict[str, int]
    songs_waiting: int = Field(description="Songs waiting for, or in, a later try.")


class SystemStatus(BaseModel):
    version: str = Field(description="songdrop's release.")
    started_at: float
    tools: list[Tool]
    disks: list[Disk]
    jobs: JobCounts
