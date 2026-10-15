"""Is this file the song its tags name? Compared with the real recording.

Shazam answers "which song is this", from its own catalogue, which also holds
re-uploads of hits under other names: it heard 21 Savage's "4L" as
"Thedjnino - Just TEN", an entry with no ISRC and no Apple Music release. So
before a file is called the wrong song, it is compared with Apple Music's
30-second preview of the song its tags name: Chromaprint fingerprints of both
(the technique behind AcoustID), the preview slid along the file, and the
share of matching fingerprint bits at the best offset. The same song scores
about 0.99, different songs about 0.55 (chance is 0.5).

The fingerprints and previews are batlab_common's (lib/batlab-common).
"""

from batlab_common import previews
from batlab_common.fingerprint import OTHER, SAME

__all__ = ["OTHER", "SAME", "compare"]


def compare(path, title, artist):
    """How much the file sounds like Apple's preview of title by artist:
    a score from 0.5 (unrelated) to 1.0, or None when Apple has no preview."""
    url = previews.apple(title, artist)
    return previews.score(path, url) if url else None
