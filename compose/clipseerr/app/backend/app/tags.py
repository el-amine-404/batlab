"""What a badge says about a video, as a tag a page can list: every badge on a
card or a video's page links to the videos that carry it too. A tag is an id
("explicit", "lang:el", "genre:Pop"), its label, and one line on what it means."""

import datetime

from .catalog import language

FIXED = {
    "explicit": ("Explicit", "Its words are marked explicit by YouTube or Apple Music."),
    "fan-upload": ("Fan upload", "Uploaded by someone other than the artist or their label."),
    "apple": ("Apple Music", "Found in Apple Music's catalog; it plays here as Apple's 30 s preview "
                             "until YouTube has it."),
    "soon": ("Coming soon", "Announced, not out yet."),
    "in-library": ("In your library", "Its song or its video is in one of your libraries."),
}


def tags_of(video, lang: str | None = None) -> set[str]:
    """The tags a video carries by itself; "in-library" is added by whoever
    knows the libraries."""
    found = set()
    if video.explicit:
        found.add("explicit")
    if not video.official:
        found.add("fan-upload")
    if video.source == "apple":
        found.add("apple")
    if video.released and video.released > datetime.date.today().isoformat():
        found.add("soon")
    if video.genre:
        found.add(f"genre:{video.genre}")
    if lang:
        found.add(f"lang:{lang}")
    return found


def describe(tag: str) -> dict | None:
    """{"id", "label", "hint"} of a tag, or None for one that isn't."""
    if tag in FIXED:
        label, hint = FIXED[tag]
    elif tag.startswith("lang:") and language.name(tag[5:]):
        label, hint = language.name(tag[5:]), f"Sung in {language.name(tag[5:])}, going by its lyrics."
    elif tag.startswith("genre:") and tag[6:].strip():
        label, hint = tag[6:], f"Apple Music files it under {tag[6:]}."
    else:
        return None
    return {"id": tag, "label": label, "hint": hint}
