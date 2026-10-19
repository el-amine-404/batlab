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


class Gone(LookupError):
    """It was there and isn't any more, for good (410); the message says
    what to do instead. again: where to ask for it once more."""

    def __init__(self, message: str, again: str | None = None, removed_at: float | None = None):
        super().__init__(message)
        self.again = again
        self.removed_at = removed_at


class Conflict(ValueError):
    """The message is shown as is."""


class Invalid(ValueError):
    """The message is shown as is."""


class Unauthorized(PermissionError):
    """Nobody known asks (401): sign in, or give an API key."""


class Forbidden(PermissionError):
    """Someone known asks for what they may not do (403). The message says what."""
