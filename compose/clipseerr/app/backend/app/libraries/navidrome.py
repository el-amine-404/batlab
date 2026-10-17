"""Navidrome, or any Subsonic server: every song it has, read a page at a
time (search3 with an empty query), matched by title and artist."""

import hashlib
import secrets

from ..integrations import Field, Invalid
from .base import FileRef, LibraryType, Snapshot

PAGE = 500
MAX_SONGS = 200_000


class Navidrome(LibraryType):
    key = "navidrome"
    label = "Navidrome"
    description = "Songs in Navidrome (or any Subsonic server), read with a user's password or token."
    homepage = "https://www.navidrome.org"
    fields = (
        Field("base_url", "Address", "url", placeholder="http://caddy:4534",
              help="Through Caddy's port 4534 on the home server: a scan asked there never reaches Navidrome."),
        Field("user", "User", "text"),
        Field("password", "Password", "secret", required=False, help="Or a token and its salt, below."),
        Field("token", "Token", "secret", required=False, help="md5(password + salt), as Subsonic apps keep it."),
        Field("salt", "Salt", "text", required=False),
    )

    def validate(self, config: dict) -> None:
        if not config.get("password") and not (config.get("token") and config.get("salt")):
            raise Invalid("Give a password, or a token with its salt.")

    def _auth(self, config: dict) -> dict:
        if config.get("password"):
            salt = secrets.token_hex(6)
            token = hashlib.md5((config["password"] + salt).encode()).hexdigest()
        else:
            salt, token = config["salt"], config["token"]
        return {"u": config["user"], "t": token, "s": salt, "v": "1.16.1", "c": "clipseerr", "f": "json"}

    async def read(self, http, config: dict, library: str = "") -> Snapshot:
        found = Snapshot()
        auth = self._auth(config)
        for offset in range(0, MAX_SONGS, PAGE):
            response = await http.get(f"{config['base_url']}/rest/search3",
                                      params={**auth, "query": "", "songCount": PAGE, "songOffset": offset,
                                              "artistCount": 0, "albumCount": 0}, timeout=30)
            response.raise_for_status()
            body = response.json().get("subsonic-response") or {}
            if body.get("status") != "ok":
                raise OSError((body.get("error") or {}).get("message") or "Navidrome refused.")
            songs = (body.get("searchResult3") or {}).get("song") or []
            for song in songs:
                file = FileRef(library=library, kind="audio", ref=song["id"], size=song.get("size"),
                               name=f"{song.get('artist') or 'Unknown'} - {song.get('title') or song['id']}"
                                    f".{song.get('suffix') or 'audio'}") if song.get("id") else None
                found.add_song(song.get("title") or "", song.get("artist") or "", file)
                # A song credited to several: each artist finds it.
                for other in song.get("artists") or []:
                    found.add_song(song.get("title") or "", other.get("name") or "", file)
            if len(songs) < PAGE:
                break
        return found

    async def open(self, http, config: dict, file: FileRef):
        # The original file, not a transcode (download, not stream).
        request = http.build_request("GET", f"{config['base_url']}/rest/download",
                                     params={**{k: v for k, v in self._auth(config).items() if k != "f"}, "id": file.ref},
                                     timeout=None)
        response = await http.send(request, stream=True)
        if response.status_code != 200 or "json" in response.headers.get("content-type", ""):
            await response.aclose()
            raise OSError(f"Navidrome doesn't have {file.name} any more.")

        async def chunks():
            try:
                async for block in response.aiter_bytes(1 << 20):
                    yield block
            finally:
                await response.aclose()

        size = response.headers.get("content-length")
        return chunks(), int(size) if size and size.isdigit() else None
