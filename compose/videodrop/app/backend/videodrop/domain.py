"""What videodrop deals in: a download (a job), where it is, and the errors
the stores raise. Pure data."""

from typing import Literal

# queued: waiting its turn (or for a retry, retry_at). running: a download
# process has it. The last three are final.
Status = Literal["queued", "running", "finished", "failed", "cancelled"]
STATUSES: tuple[Status, ...] = ("queued", "running", "finished", "failed", "cancelled")
ACTIVE: tuple[Status, ...] = ("queued", "running")
FINAL: tuple[Status, ...] = ("finished", "failed", "cancelled")

# Where a running download is. processing: ffmpeg joins the streams, embeds
# the cover, subtitles and tags; placing: its files are moved where they go.
Stage = Literal["starting", "downloading", "processing", "placing"]

FileKind = Literal["video", "subtitles", "other"]


class NotFound(LookupError):
    """The message is shown as is."""


class Conflict(ValueError):
    """The message is shown as is."""


class Invalid(ValueError):
    """The message is shown as is."""
