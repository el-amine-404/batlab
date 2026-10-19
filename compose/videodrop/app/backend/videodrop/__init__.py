"""videodrop: getting videos, for whoever asks. An app (or a person, on the
page) asks for a link, with the options and the destination it wants;
videodrop downloads it with yt-dlp, a few at once, says how it goes (a
callback, a live stream, the API) and gives the file back. It knows videos
and where they go, nothing about what they are for (docs/services.md)."""

import os

# The date and commit of its last change, "2026.10.19-d4dfa93", given to the
# image when it is built (the Makefile's BATLAB_REVISION); "dev" otherwise.
__version__ = os.environ.get("VIDEODROP_VERSION") or "dev"
