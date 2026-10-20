"""videodrop: getting videos, for whoever asks. An app (or a person, on the
page) asks for a link, with the options and the destination it wants;
videodrop downloads it with yt-dlp, a few at once, says how it goes (a
callback, a live stream, the API) and gives the file back. It knows videos
and where they go, nothing about what they are for (docs/services.md)."""

# Semantic: a feat commit raises the minor, a fix the patch (as clipseerr's).
__version__ = "1.11.1"
