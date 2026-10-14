"""Downloads a Spotify link with spotDL's Python API (run with spotDL's own
interpreter, /app/.venv/bin/python, so its pinned libraries stay its own).

stdin: {"url", "settings", "max_items", "parallel"}; events: see protocol.py.
"""

import asyncio
import logging
import sys
import traceback

import verify
from protocol import Events, read_config

RETRY_PAUSES = (3, 10)


def _song_name(song) -> str:
    artists = ", ".join(song.artists or []) or song.artist or ""
    return f"{artists} - {song.name}" if artists else (song.name or song.url or "?")


def _error_for(downloader, song, settings) -> str:
    # spotDL keeps "<song url> - <Exception>: <message>" for songs that failed.
    prefix = f"{song.url} - "
    for entry in reversed(downloader.errors):
        if entry.startswith(prefix):
            return entry[len(prefix):]
    if song.explicit and settings.get("skip_explicit"):
        return "Skipped: marked explicit."
    for entry in reversed(downloader.errors):
        if song.name and song.name in entry:
            return entry
    return "spotDL could not download this song."


def main() -> None:
    config = read_config()
    events = Events.from_env()
    try:
        from spotdl import Spotdl
        from spotdl.utils.config import DOWNLOADER_OPTIONS, SPOTIFY_OPTIONS
    except Exception as exc:  # the image is broken; nothing to retry
        events.fatal(f"spotDL failed to load: {exc}")

    # spotDL reports a failed download as "YT-DLP download error" and logs
    # yt-dlp's actual reason at debug level only; that reason goes to the
    # container log (songdrop logs a job's engine output when a song fails).
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    provider_log = logging.getLogger("spotdl.providers.audio")
    provider_log.addHandler(handler)
    provider_log.setLevel(logging.DEBUG)

    settings = dict(DOWNLOADER_OPTIONS)
    settings.update(config["settings"])
    try:
        client = Spotdl(
            client_id=SPOTIFY_OPTIONS["client_id"],
            client_secret=SPOTIFY_OPTIONS["client_secret"],
            no_cache=True,
            headless=True,
            downloader_settings=settings,
        )
        songs = client.search([config["url"]])
    except Exception as exc:
        traceback.print_exc()
        events.fatal(f"Spotify couldn't read this link: {exc}")

    if not songs:
        events.fatal("Spotify returned no songs for this link.")
    if len(songs) > config["max_items"]:
        events.fatal(f"This link holds {len(songs)} songs; the limit is {config['max_items']}.")

    first = songs[0]
    title = _song_name(first) if len(songs) == 1 else (first.list_name or first.album_name or "Spotify list")
    events.emit("meta", title=title, total=len(songs))

    downloader = client.downloader
    semaphore = asyncio.Semaphore(max(1, int(config["parallel"])))

    loop = downloader.loop
    wrong_audio: set[int] = set()  # no retry: it was downloaded, as another song

    async def download(song):
        """(song, path) or (song, None, why it failed)."""
        try:
            done, path = await downloader.async_search_and_download(song)
        except Exception as exc:
            traceback.print_exc()
            return song, None, f"{type(exc).__name__}: {exc}"
        if path is not None and path.is_file():
            return done, path, None
        return done, None, _error_for(downloader, done, settings)

    async def sounds_right(song, path) -> bool:
        """False only when Spotify's preview says it is another recording."""
        score = await loop.run_in_executor(None, verify.score, str(path), song.song_id)
        print(f"{_song_name(song)}: {song.download_url} scores "
              f"{'no preview' if score is None else f'{score:.2f}'} against Spotify's preview", flush=True)
        if score is None or score >= verify.OTHER:
            return True
        path.unlink(missing_ok=True)
        return False

    async def attempt(index: int, song) -> str | None:
        """Downloads one song; returns why it failed, or None. An upload that
        does not sound like Spotify's preview is replaced by the next one of
        the same length that does."""
        done, path, error = await download(song)
        if error:
            return error
        if not await sounds_right(done, path):
            tried = {done.download_url}
            try:
                others = await loop.run_in_executor(None, verify.candidates, done, tried)
            except Exception as exc:
                traceback.print_exc()
                others = []
            path = None
            for url in others:
                done.download_url = url
                done, path, error = await download(done)
                if path is not None and await sounds_right(done, path):
                    break
                path = None
            if path is None:
                wrong_audio.add(index)
                return (f"Every YouTube upload tried ({1 + len(others)}) sounds like another "
                        f"song than Spotify's preview.")
        events.emit(
            "file", index=index, path=str(path), title=done.name,
            artist=", ".join(done.artists or []) or done.artist,
        )
        return None

    async def first_pass(index: int, song):
        async with semaphore:
            events.emit("item_start", index=index, name=_song_name(song))
            return index, song, await attempt(index, song)

    async def run_all() -> None:
        results = await asyncio.gather(*(first_pass(index, song) for index, song in enumerate(songs)))
        # YouTube answers some audio requests with 403 at random (4 of an
        # album's 15 on 2026-10-14, each fine when tried again): up to two
        # more tries, one song at a time, with a pause before each.
        for index, song, error in results:
            if error is None:
                continue
            if not (song.explicit and settings.get("skip_explicit")) and index not in wrong_audio:
                for pause in RETRY_PAUSES:
                    print(f"retrying {_song_name(song)} in {pause}s after: {error}", flush=True)
                    await asyncio.sleep(pause)
                    events.emit("item_start", index=index, name=_song_name(song))
                    error = await attempt(index, song)
                    if error is None:
                        break
            if error is not None:
                events.emit("item_error", index=index, name=_song_name(song), message=error)

    loop.run_until_complete(run_all())


if __name__ == "__main__":
    main()
