#!/usr/bin/env python3
"""Fill missing subtitle languages with AI translations, and keep looking for
human subtitles to replace them.

Bazarr wants English, French and Arabic for every video. When no site has a
language, this asks Bazarr to translate the video's English subtitle into it
(Bazarr → Settings → Subtitles → Translating: Gemini). Every 6 hours:

1. Translate. For each language on Bazarr's wanted list, at most --translate
   per run, once:
   - the video is GRACE_DAYS old, so Bazarr's searches go first;
   - subextract has handled this very file (its state says so), so a language
     the release carries inside the video is already a sidecar, and an
     incomplete run (a track refused or skipped) leaves the video alone;
   - no sidecar in that language is on disk, whatever Bazarr's list says:
     Bazarr writes the translation as <video>.<lang>.srt, over anything there;
   - an English sidecar subextract checked against the audio, and not itself
     a translation, is there to translate from (not hearing-impaired when
     there is a choice: sound descriptions translate badly).
   A translation that doesn't appear is tried again a day later, three times
   in all.
2. Tag. Bazarr opens every translation with a cue, "# Subtitles translated with
   <model> #", shown for the first seconds. That cue is what marks a file as
   AI-made, for viewers and for this job; Bazarr's history (action 6) says
   which video and language it belongs to.
3. Keep looking. Each AI subtitle is searched for again through Bazarr's
   manual search every --research-days. A result in its language at Bazarr's
   own minimum score or better replaces it. Bazarr's upgrades stop after at
   most 30 days; this doesn't.

What it did goes to the downloads channel in one message per run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib/batlab-common"))
from batlab_common import notify as apprise  # noqa: E402

MARKER = "# Subtitles translated with "
SOURCE_LANGUAGE = "en"
TRANSLATED = 6           # Bazarr's history action for a translation
GRACE_DAYS = 7
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".sup"}
RETRY_SECONDS = 24 * 3600
MAX_TRIES = 3
DAY = 24 * 3600


def env_value(env_file: Path, name: str) -> str:
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


class Bazarr:
    def __init__(self, base_url: str, key: str) -> None:
        self.base_url, self.key = base_url.rstrip("/"), key

    def request(self, method: str, endpoint: str, timeout: int = 60, **query: object) -> object:
        url = f"{self.base_url}/api/{endpoint}"
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        request = urllib.request.Request(url, method=method, headers={"X-API-KEY": self.key})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
        return json.loads(payload) if payload else None

    def get(self, endpoint: str, timeout: int = 60, **query: object) -> object:
        return self.request("GET", endpoint, timeout, **query)


class Media:
    """Bazarr's two kinds of video, with the names its API uses for each."""

    def __init__(self, kind: str) -> None:
        self.kind = kind  # "episode" or "movie"
        self.plural = kind + "s"
        self.id_name = "sonarrEpisodeId" if kind == "episode" else "radarrId"
        self.list_query = "episodeid[]" if kind == "episode" else "radarrid[]"
        self.search_query = "episodeid" if kind == "episode" else "radarrid"
        self.score_setting = "minimum_score" if kind == "episode" else "minimum_score_movie"

    def title(self, row: dict) -> str:
        if self.kind == "episode":
            return f"{row.get('seriesTitle', '?')} {row.get('episode_number', '')}".strip()
        return row.get("title", "?")

    def download_query(self, row: dict) -> dict:
        if self.kind == "episode":
            return {"seriesid": row["sonarrSeriesId"], "episodeid": row["sonarrEpisodeId"]}
        return {"radarrid": row["radarrId"]}


KINDS = (Media("episode"), Media("movie"))


def host_path(path: str, data_root: Path) -> Path:
    """Bazarr's /data/… path on this machine."""
    if not path.startswith("/data/"):
        raise ValueError(f"not under /data: {path}")
    return data_root / path[len("/data/"):]


def is_translation(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return MARKER.encode() in file.read(600)
    except OSError:
        return False


def file_stamp(path: Path) -> list[int]:
    """As subextract records a file: size, modification time, inode."""
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino]


def checked_by_subextract(video: Path, handled: dict) -> dict | None:
    """subextract's record of this very file, when its last run on it was
    complete; else None."""
    record = handled.get(str(video))
    try:
        return record if record and record.get("video") == file_stamp(video) else None
    except OSError:
        return None


def has_sidecar(video: Path, language: str) -> bool:
    """A subtitle file in that language next to the video (.fr.srt, .fr.hi.ass…)."""
    prefix = f"{video.stem}.{language}."
    return any(path.name.startswith(prefix) and path.suffix.lower() in SUBTITLE_EXTENSIONS
               for path in video.parent.iterdir())


def english_source(item: dict, data_root: Path, record: dict) -> str | None:
    """The Bazarr path of the video's English sidecar that subextract checked
    against the audio (unchanged since), not hearing-impaired when there is a
    choice, and never one that is itself a translation."""
    checked = record.get("sidecars") or {}

    def usable(path: str) -> bool:
        local = host_path(path, data_root)
        try:
            return checked.get(str(local)) == file_stamp(local) and not is_translation(local)
        except OSError:
            return False

    found = [s for s in item.get("subtitles") or []
             if s.get("code2") == SOURCE_LANGUAGE and s.get("path") and not s.get("forced") and usable(s["path"])]
    found.sort(key=lambda s: bool(s.get("hi")))
    return found[0]["path"] if found else None


def ai_subtitles(bazarr, media: Media, data_root: Path) -> dict[str, dict]:
    """{subtitle path: history row} of Bazarr's translations still on disk
    with their cue: a replaced one no longer has it."""
    found = {}
    for row in bazarr.get(f"{media.plural}/history", start=0, length=-1)["data"]:
        path = row.get("subtitles_path")
        if row.get("action") == TRANSLATED and path and path not in found \
                and is_translation(host_path(path, data_root)):
            found[path] = row
    return found


def translate_missing(bazarr, media: Media, data_root: Path, state: dict, handled: dict, now: float, limit: int,
                      apply: bool, log=print) -> tuple[list[str], list[str]]:
    """Ask Bazarr to translate wanted languages. Returns what was asked, and
    what is still wanted a day after its last try (reported once)."""
    wanted = bazarr.get(f"{media.plural}/wanted", start=0, length=-1)["data"]
    candidates = [(row, missing["code2"]) for row in wanted for missing in row.get("missing_subtitles") or []
                  if missing["code2"] != SOURCE_LANGUAGE and not missing.get("forced") and not missing.get("hi")]
    attempts = state.setdefault("attempts", {})
    still_wanted = {f"{media.kind}:{row[media.id_name]}:{language}" for row, language in candidates}
    for key in [key for key in attempts if key.startswith(media.kind + ":") and key not in still_wanted]:
        del attempts[key]  # translated, or found by Bazarr
    asked: list[str] = []
    gave_up: list[str] = []
    details: dict[int, dict] = {}
    for row, language in candidates:
        if len(asked) >= limit:
            break
        key = f"{media.kind}:{row[media.id_name]}:{language}"
        tried = attempts.get(key, {"count": 0, "at": 0})
        if now - tried["at"] < RETRY_SECONDS:
            continue
        if tried["count"] >= MAX_TRIES:
            if not tried.get("reported"):
                gave_up.append(f"{media.title(row)} → {language}")
                tried["reported"] = apply
            continue
        media_id = row[media.id_name]
        if media_id not in details:
            details[media_id] = bazarr.get(media.plural, **{media.list_query: [media_id]})["data"][0]
        item = details[media_id]
        video = host_path(item["path"], data_root)
        try:
            age = now - video.stat().st_mtime
        except OSError:
            continue
        if age < GRACE_DAYS * DAY or has_sidecar(video, language):
            continue
        record = checked_by_subextract(video, handled)
        source = english_source(item, data_root, record) if record else None
        if not source:
            continue
        log(f"translate: {media.title(row)} {SOURCE_LANGUAGE} → {language}")
        if apply:
            bazarr.request("PATCH", "subtitles", timeout=60, action="translate", language=language, path=source,
                           type=media.kind, id=media_id, forced="False", hi="False", original_format="False")
            attempts[key] = {"count": tried["count"] + 1, "at": now}
        asked.append(f"{media.title(row)} → {language}")
    return asked, gave_up


def look_for_humans(bazarr, media: Media, ai: dict[str, dict], data_root: Path, state: dict, now: float,
                    minimum: int, limit: int, every_days: float, apply: bool, log=print) -> list[str]:
    """Search again for the AI subtitles searched longest ago; download a
    human one in the same language when it reaches Bazarr's minimum score."""
    searched = state.setdefault("searched", {})
    due = sorted((path for path in ai if now - searched.get(path, 0) >= every_days * DAY),
                 key=lambda path: searched.get(path, 0))
    replaced = []
    for path in due[:limit]:
        row = ai[path]
        language = row["language"]["code2"]
        results = bazarr.get(f"providers/{media.plural}", timeout=300, **{media.search_query: row[media.id_name]})["data"]
        human = [r for r in results if r.get("language") == language and str(r.get("forced")) == "False"
                 and str(r.get("hearing_impaired")) == "False" and float(r.get("score") or 0) >= minimum]
        searched[path] = now
        if not human:
            continue
        best = max(human, key=lambda r: float(r["score"]))
        log(f"replace: {media.title(row)} {language} with {best['provider']} ({best['score']}%)")
        if apply:
            bazarr.request("POST", f"providers/{media.plural}", timeout=300, hi="False", forced="False",
                           original_format="False", provider=best["provider"], subtitle=best["subtitle"],
                           **media.download_query(row))
            if is_translation(host_path(path, data_root)):
                log(f"replace: {path} still has the translation cue")
                continue
        replaced.append(f"{media.title(row)} {language} ({best['provider']}, {best['score']}%)")
    return replaced


def report(translated: list[str], replaced: list[str], gave_up: list[str], dry_run: bool) -> None:
    fields = [(name, "\n".join(lines[:15]) + (f"\n… and {len(lines) - 15} more" if len(lines) > 15 else ""))
              for name, lines in (("Translated by AI", translated), ("Replaced by a human subtitle", replaced),
                                  ("Couldn't translate (3 tries)", gave_up)) if lines]
    if not fields or dry_run:
        return
    title = "Subtitles: " + ", ".join(f"{len(lines)} {word}" for lines, word in
                                       ((translated, "translated"), (replaced, "replaced"), (gave_up, "failed"))
                                       if lines)
    try:
        apprise.deliver("subtranslate", "downloads", title, apprise.fields_text(fields), "info")
    except apprise.NotSent as error:
        print(f"subtranslate: {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print what it would do; change nothing, post nothing")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--data-root", default="/mnt/storage/data")
    parser.add_argument("--bazarr-url", default="http://bazarr.homelab.lan")
    parser.add_argument("--translate", type=int, default=6, help="translations asked per run (Gemini's quota)")
    parser.add_argument("--searches", type=int, default=8, help="AI subtitles searched again per run")
    parser.add_argument("--research-days", type=float, default=7, help="days between searches for one AI subtitle")
    parser.add_argument("--subextract-state", default=os.path.join(
        os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"), "batlab-subextract/handled.json"))
    parser.add_argument("--state-dir", default=os.path.join(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"),
                                                            "batlab-subtranslate"))
    args = parser.parse_args(argv)
    data_root, apply = Path(args.data_root), not args.dry_run
    if not (data_root / "media").is_dir():
        print(f"{data_root}/media is not there; is the data disk mounted?", file=sys.stderr)
        return 1
    bazarr = Bazarr(args.bazarr_url, env_value(Path(args.env_file), "BAZARR_API_KEY"))
    state_path = Path(args.state_dir) / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    now = time.time()
    try:
        handled = json.loads(Path(args.subextract_state).read_text())
    except (OSError, ValueError) as error:
        print(f"subextract's state can't be read ({error}); not translating", file=sys.stderr)
        handled = {}
    # The first run reports no translation: those were there before it.
    known = set(state["ai"]) if "ai" in state else None

    translated, replaced, gave_up, failed = [], [], [], 0
    try:
        settings = bazarr.get("system/settings")
        if apply and settings["translator"]["translator_type"] != "gemini":
            print("Bazarr's translator isn't Gemini (Settings → Subtitles → Translating); not translating",
                  file=sys.stderr)
            args.translate = 0
        ai_now = set()
        for media in KINDS:
            ai = ai_subtitles(bazarr, media, data_root)
            ai_now |= set(ai)
            if known is not None:
                translated += [f"{media.title(row)} → {row['language']['code2']}"
                               for path, row in ai.items() if path not in known]
            replaced += look_for_humans(bazarr, media, ai, data_root, state, now,
                                        int(settings["general"][media.score_setting]), args.searches,
                                        args.research_days, apply)
            asked, missed = translate_missing(bazarr, media, data_root, state, handled, now, args.translate,
                                              apply)
            args.translate -= len(asked)
            gave_up += missed
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        print(f"subtranslate: Bazarr: {error}", file=sys.stderr)
        failed = 1
    else:
        state["ai"] = sorted(ai_now)
        state["searched"] = {path: at for path, at in state.get("searched", {}).items() if path in ai_now}

    if apply:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
        temporary.replace(state_path)
    report(translated, replaced, gave_up, args.dry_run)
    return failed


if __name__ == "__main__":
    sys.exit(main())
