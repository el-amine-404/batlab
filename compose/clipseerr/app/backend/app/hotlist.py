"""The Hot category's own state, in the database: the videos added to it by
hand (a video no feed or search found), and every artist it has shown, kept
even after their videos leave it."""

import threading
import time

from .catalog import match
from .db import Database

PICKS = "hot_picks"
ARTISTS = "hot_artists"
CHANGES = "hot_changes"
MAX_PICKS = 1000
# The artists Hot has shown: the ones it showed longest ago go first.
MAX_ARTISTS = 2000


def _names(video: dict) -> list[tuple[str, str | None]]:
    """(name, YouTube Music id) of each artist of a video, as a list gives it."""
    found = []
    for ref in video.get("artists") or []:
        name = match.channel_artist(ref.get("name") or "")
        if name and name != "Unknown artist":
            # A channel's id is an artist page's only when its name is the artist's ("…VEVO" isn't).
            found.append((name, ref.get("id") if name == (ref.get("name") or "").strip() else None))
    return found


class HotList:
    def __init__(self, db: Database):
        self._db = db
        self._lock = threading.Lock()

    # ---- picks ------------------------------------------------------------------------

    def picks(self) -> list[dict]:
        """The picked videos (Video dicts), newest pick first."""
        return self._db.get(PICKS, []) or []

    def pick_ids(self) -> list[str]:
        return [video["id"] for video in self.picks()]

    def pick(self, video: dict) -> None:
        with self._lock:
            picks = [v for v in self.picks() if v["id"] != video["id"]]
            self._db.put(PICKS, [{**video, "picked_at": time.time()}, *picks][:MAX_PICKS])

    def unpick(self, video_id: str) -> bool:
        with self._lock:
            picks = self.picks()
            kept = [v for v in picks if v["id"] != video_id]
            self._db.put(PICKS, kept)
            return len(kept) != len(picks)

    # ---- changes ------------------------------------------------------------------------

    def changes(self, ids: list[str]) -> dict:
        """When Hot's videos last changed, and by how many: ids are what it
        has now. {"changed_at": None, …} until a change is seen."""
        now = time.time()
        new = set(ids)
        with self._lock:
            old = self._db.get(CHANGES)
            if old is None:
                old = {"ids": sorted(new), "changed_at": None, "added": 0, "removed": 0}
                self._db.put(CHANGES, old)
            elif new != set(old["ids"]):
                before = set(old["ids"])
                old = {"ids": sorted(new), "changed_at": now, "added": len(new - before), "removed": len(before - new)}
                self._db.put(CHANGES, old)
        return {key: old[key] for key in ("changed_at", "added", "removed")}

    def last_changes(self) -> dict:
        """The last changes() found, without counting: a list made with a source failing proves nothing."""
        found = self._db.get(CHANGES) or {"changed_at": None, "added": 0, "removed": 0}
        return {key: found[key] for key in ("changed_at", "added", "removed")}

    # ---- artists ------------------------------------------------------------------------

    def artists(self) -> list[dict]:
        """Every artist Hot has shown, the most videos first."""
        found = list((self._db.get(ARTISTS, {}) or {}).values())
        return sorted(found, key=lambda a: (-a["videos"], -a["last_seen"], a["name"].casefold()))

    def saw(self, videos: list[dict]) -> None:
        """Counts the artists of what Hot shows now; artists it showed before stay."""
        now = time.time()
        counts: dict[str, dict] = {}
        for video in videos:
            for name, artist_id in _names(video):
                entry = counts.setdefault(name.casefold(), {"name": name, "id": artist_id, "videos": 0})
                entry["videos"] += 1
                entry["id"] = entry["id"] or artist_id
        with self._lock:
            known = self._db.get(ARTISTS, {}) or {}
            for key, seen in counts.items():
                old = known.get(key) or {"first_seen": now, "thumbnail": None, "id": None}
                known[key] = {**old, "name": old.get("name") or seen["name"], "id": seen["id"] or old.get("id"),
                              "videos": max(seen["videos"], old.get("videos", 0)), "last_seen": now}
            if len(known) > MAX_ARTISTS:
                kept = sorted(known.items(), key=lambda item: -item[1]["last_seen"])[:MAX_ARTISTS]
                known = dict(kept)
            self._db.put(ARTISTS, known)

    def unresolved(self, limit: int) -> list[str]:
        """Names whose YouTube Music page isn't known yet (or their picture), most seen first."""
        return [a["name"] for a in self.artists() if a.get("id") is None or a.get("thumbnail") is None][:limit]

    def resolved(self, name: str, artist_id: str | None, thumbnail: str | None) -> None:
        with self._lock:
            known = self._db.get(ARTISTS, {}) or {}
            entry = known.get(name.casefold())
            if entry:
                # "" marks a name looked up in vain: not asked again every round.
                entry["id"] = entry.get("id") or artist_id or ""
                entry["thumbnail"] = thumbnail or entry.get("thumbnail") or ""
                self._db.put(ARTISTS, known)
