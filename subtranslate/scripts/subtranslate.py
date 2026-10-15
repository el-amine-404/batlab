#!/usr/bin/env python3
"""Fill missing subtitle languages with AI translations, and keep looking for
human subtitles to replace them.

Bazarr wants English, French and Arabic for every video; some languages exist
on no subtitle site. Every 6 hours, holding subextract's lock so the two never
run at once:

1. Translate. For each language on Bazarr's wanted list, at most --translate
   per run, once:
   - the video is GRACE_DAYS old, so Bazarr's searches go first;
   - subextract has handled this very file completely (its state says so), so
     a language the release carries inside the video is already a sidecar;
   - no subtitle in that language is next to the video;
   - an English sidecar subextract checked against the audio, unchanged since
     and not itself a translation, is there to translate from (a plain one
     rather than hearing-impaired: sound descriptions translate badly).
   Gemini gets the show, its summary, the episode's title, names to keep, and
   the dialogue 400 lines at a time; it returns the text of each line, and the
   numbering and timing are copied from the English by code. An answer missing
   a line, or with an empty one, is refused. Lines harder to read than the
   English (over MAX_CPS characters a second and longer than the English line)
   go back to Gemini once, to be shortened with the same meaning; the timing
   never changes. The file is written in one step
   that never replaces a file: a subtitle Bazarr wrote meanwhile wins, and one
   Bazarr writes later replaces the translation, as it should.
2. Tag. Every translation opens with a line, "Translated by AI (<model>)",
   shown for the first seconds. It marks the file for whoever watches and for
   this job.
3. Keep looking. Each AI subtitle is searched for again every --research-days
   through Bazarr's manual search; a result in its language at Bazarr's own
   minimum score replaces it. Bazarr refuses AI-made subtitles from its sites,
   so the replacement is a human one.

What it did goes to the downloads channel in one message per run. Gemini's key
and model are GEMINI_API_KEY and GEMINI_MODEL in compose/.env; on a project
with no billing account nothing can be charged, and a refusal for quota, an overload
or no answer ends the run's translations without counting a try.
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib/batlab-common"))
from batlab_common import notify as apprise  # noqa: E402

TAG = "Translated by AI"
MARKERS = (TAG, "# Subtitles translated with ")  # ours, and Bazarr's own translator
SOURCE_LANGUAGE = "en"
LANGUAGES = {"fr": "French", "ar": "Modern Standard Arabic", "es": "Spanish", "de": "German", "it": "Italian",
             "pt": "Portuguese", "ja": "Japanese"}
GRACE_DAYS = 7
RETRY_SECONDS = 24 * 3600
MAX_TRIES = 3
DAY = 24 * 3600
CHUNK = 400
# Characters a second an adult reads comfortably (Netflix's guideline for English). 17, the usual figure
# for other languages, is broken by a quarter of the English lines in anime: a translation is held to
# the English it comes from, never asked to be shorter than it.
MAX_CPS = 20
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".sup"}
TIMING = re.compile(r"^\s*(\d+:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d+:\d{2}:\d{2}[,.]\d{3})")
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiUnavailable(RuntimeError):
    """Gemini over quota, overloaded or unreachable: no more translations
    this run, and no try counted against the video."""


class TranslationFailed(RuntimeError):
    pass


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

    def show(self, bazarr, row: dict) -> dict:
        """The series or the film: title, year, overview."""
        if self.kind == "episode":
            return bazarr.get("series", **{"seriesid[]": [row["sonarrSeriesId"]]})["data"][0]
        return bazarr.get("movies", **{"radarrid[]": [row["radarrId"]]})["data"][0]

    def rescan(self, bazarr, info: dict) -> None:
        if self.kind == "episode":
            bazarr.request("PATCH", "series", seriesid=info["series"], action="scan-disk")
        else:
            bazarr.request("PATCH", "movies", radarrid=info["id"], action="scan-disk")

    def download_query(self, info: dict) -> dict:
        if self.kind == "episode":
            return {"seriesid": info["series"], "episodeid": info["id"]}
        return {"radarrid": info["id"]}


KINDS = {"episode": Media("episode"), "movie": Media("movie")}


def host_path(path: str, data_root: Path) -> Path:
    """Bazarr's /data/… path on this machine."""
    if not path.startswith("/data/"):
        raise ValueError(f"not under /data: {path}")
    return data_root / path[len("/data/"):]


def is_translation(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            start = file.read(600)
    except OSError:
        return False
    return any(marker.encode() in start for marker in MARKERS)


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


def english_source(video: Path, record: dict) -> Path | None:
    """The English SRT next to the video that subextract checked against the
    audio and that hasn't changed since, plain before hearing-impaired, never
    a translation."""
    found = []
    for name, stamp in (record.get("sidecars") or {}).items():
        path = Path(name)
        parts = path.name[len(video.stem) + 1:].lower().split(".") if path.name.startswith(video.stem + ".") else []
        if parts[:1] != [SOURCE_LANGUAGE] or path.suffix.lower() != ".srt" or "forced" in parts:
            continue
        try:
            if file_stamp(path) == stamp and not is_translation(path):
                found.append(("hi" in parts or "sdh" in parts, path.name, path))
        except OSError:
            continue
    return min(found)[2] if found else None


# --- subtitles ------------------------------------------------------------------

def parse_srt(text: str) -> list[tuple[str, str, str]]:
    """(start, end, text) of each cue with text."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")):
        lines = block.strip("\n").split("\n")
        for number, line in enumerate(lines[:2]):
            match = TIMING.match(line)
            if match:
                words = "\n".join(lines[number + 1:]).strip()
                if words:
                    cues.append((match[1].replace(".", ","), match[2].replace(".", ","), words))
                break
    return cues


def milliseconds(stamp: str) -> int:
    hours, minutes, rest = stamp.split(":")
    seconds, millis = rest.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def timestamp(ms: int) -> str:
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def compose_srt(cues: list[tuple[str, str, str]], tag: str) -> str:
    """The tag for the first seconds (at most 4, before the first line when
    there is room), then the cues as given."""
    first = milliseconds(cues[0][0]) if cues else 4000
    end = max(min(first, 4000), 1500)
    blocks = [f"1\n{timestamp(0)} --> {timestamp(end)}\n{tag}\n"]
    blocks += [f"{number}\n{start} --> {stop}\n{words}\n" for number, (start, stop, words) in enumerate(cues, 2)]
    return "\n".join(blocks)


def names_to_keep(cues: list[tuple[str, str, str]], limit: int = 25) -> list[str]:
    """Capitalised words met mid-sentence at least twice: mostly names."""
    counts = collections.Counter()
    for _, _, words in cues:
        for match in re.finditer(r"(?<=[a-z,] )([A-Z][a-z]{2,})\b", words):
            counts[match[1]] += 1
    return [name for name, count in counts.most_common(limit) if count >= 2]


def instructions(language: str, show: dict, kind: str, episode_title: str, names: list[str]) -> str:
    what = "the TV series" if kind == "episode" else "the film"
    text = [f"You translate subtitles from English into {language} for {what} "
            f"\"{show.get('title', '')}\" ({show.get('year', '')})."]
    if show.get("overview"):
        text.append(f"What it is about: {show['overview']}")
    if episode_title:
        text.append(f"This episode is \"{episode_title}\".")
    if names:
        text.append("Names that appear (keep them as they are, or in their usual spelling in "
                    f"{language}): {', '.join(names)}.")
    text.append(
        "The input is a JSON array of subtitle lines, {\"i\": number, \"t\": English text}, in order. Answer with "
        "the same array: every \"i\" once, in the same order, with \"t\" in " + language + ". "
        "Translate meaning and tone as a professional subtitler would: natural, concise, easy to read at a "
        "glance, with the register each speaker uses. Keep each line's meaning in its own line; don't move text "
        "between lines, merge or split them. Keep tags such as <i>…</i> and line breaks. Translate song lyrics "
        "and bracketed sound descriptions too. Never add notes or explanations.")
    return "\n\n".join(text)


class Gemini:
    def __init__(self, key: str, model: str, timeout: int = 300) -> None:
        self.key, self.model, self.timeout = key, model, timeout
        self.requests = 0

    def __call__(self, system: str, lines: list[dict]) -> list[dict]:
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(lines, ensure_ascii=False)}]}],
                "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json",
                                     "responseSchema": {"type": "ARRAY", "items": {
                                         "type": "OBJECT", "required": ["i", "t"],
                                         "properties": {"i": {"type": "INTEGER"}, "t": {"type": "STRING"}}}}}}
        request = urllib.request.Request(GEMINI.format(model=urllib.parse.quote(self.model)), method="POST",
                                         data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        self.requests += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                answer = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(2000).decode(errors="replace")
            if error.code == 429 or error.code >= 500:
                raise GeminiUnavailable(f"Gemini answered {error.code}: {detail[:200]}") from error
            raise TranslationFailed(f"Gemini answered {error.code}: {detail[:300]}") from error
        except (urllib.error.URLError, OSError) as error:
            raise GeminiUnavailable(f"Gemini unreachable: {getattr(error, 'reason', error)}") from error
        candidate = (answer.get("candidates") or [{}])[0]
        if candidate.get("finishReason") not in (None, "STOP"):
            raise TranslationFailed(f"Gemini stopped: {candidate.get('finishReason')}")
        text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []))
        try:
            return json.loads(text)
        except ValueError as error:
            raise TranslationFailed("Gemini's answer isn't the JSON asked for") from error


def read_answer(answer: object, wanted: set[int]) -> dict[int, str]:
    """{line number: text} from Gemini's answer; TranslationFailed when a
    line is missing, doubled, unknown or empty."""
    if not isinstance(answer, list):
        raise TranslationFailed("Gemini's answer isn't a list of lines")
    texts = {}
    for line in answer:
        if not isinstance(line, dict) or not isinstance(line.get("t"), str) or line.get("i") in texts:
            raise TranslationFailed("Gemini's answer has a malformed or doubled line")
        texts[line["i"]] = line["t"].strip()
    if set(texts) != wanted or not all(texts.values()):
        missing = len(wanted - set(texts)) + sum(1 for i in wanted if not texts.get(i))
        raise TranslationFailed(f"Gemini's answer lacks {missing} of {len(wanted)} lines")
    return texts


def translate_cues(translator, system: str, cues: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """The cues with their text translated, CHUNK lines per request; timing
    and order are the English's."""
    out = []
    for first in range(0, len(cues), CHUNK):
        part = cues[first:first + CHUNK]
        lines = [{"i": first + number, "t": words} for number, (_, _, words) in enumerate(part)]
        texts = read_answer(translator(system, lines), {line["i"] for line in lines})
        out += [(start, stop, texts[first + number]) for number, (start, stop, _) in enumerate(part)]
    return out


def visible(text: str) -> int:
    """Characters a viewer reads: tags left out, a line break as a space."""
    return len(re.sub(r"<[^>]+>", "", text).replace("\n", " ").strip())


def room(start: str, stop: str) -> int:
    """Characters a line on screen from start to stop can hold at MAX_CPS."""
    return max(1, (milliseconds(stop) - milliseconds(start)) * MAX_CPS // 1000)


def shortening_instructions(language: str) -> str:
    return (f"These {language} subtitle lines, translated from English, are too long to read in the time they "
            f"are on screen. Give each a shorter {language} line with the same meaning, at most \"max\" "
            "characters; \"en\" is the English, for the meaning. Drop filler, repetition and what the picture "
            "already shows; keep names and tags such as <i>…</i>. The input is a JSON array of {\"i\", \"t\", "
            "\"en\", \"max\"}; answer with an array of {\"i\", \"t\"}: every \"i\" once, in the same order. "
            "Never add notes or explanations.")


def shorten(translator, language: str, english: list[tuple[str, str, str]],
            translated: list[tuple[str, str, str]], log=print) -> tuple[list[tuple[str, str, str]], bool]:
    """The translation with its lines harder to read than the English
    shortened by a second request: over MAX_CPS for their time on screen and
    longer than the English line, down to whichever is longer. Timing
    unchanged. A line comes back only shorter or not at all, and a failed
    request keeps the long lines. Returns (cues, stopped), stopped on a
    refusal for quota or load."""
    limit = [max(room(start, stop), visible(words)) for start, stop, words in english]
    fast = [n for n, (_, _, words) in enumerate(translated) if visible(words) > limit[n]]
    if not fast:
        return translated, False
    out, shortened, stopped = list(translated), 0, False
    system = shortening_instructions(language)
    for first in range(0, len(fast), CHUNK):
        numbers = fast[first:first + CHUNK]
        lines = [{"i": n, "t": translated[n][2], "en": english[n][2], "max": limit[n]} for n in numbers]
        try:
            texts = read_answer(translator(system, lines), set(numbers))
        except GeminiUnavailable:
            stopped = True
            break
        except TranslationFailed as error:
            log(f"reading speed: {error}; long lines kept")
            continue
        for n in numbers:
            if visible(texts[n]) < visible(out[n][2]):
                out[n] = (out[n][0], out[n][1], texts[n])
                shortened += 1
    still = sum(1 for n, (_, _, words) in enumerate(out) if visible(words) > limit[n])
    log(f"reading speed: {len(fast)} line(s) harder to read than the English, {shortened} shortened, "
        f"{still} still over")
    return out, stopped


def publish(path: Path, text: str) -> bool:
    """Write a new file in one step; False, leaving everything as it was,
    when one is already there."""
    descriptor, temporary = tempfile.mkstemp(prefix=".subtranslate-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)  # never replaces an existing file
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temporary)


# --- the run ----------------------------------------------------------------------

def candidates(bazarr, media: Media, data_root: Path, handled: dict, state: dict, now: float):
    """(row, language, video, English source) for each wanted language this
    job may translate now; attempts no longer wanted are forgotten."""
    wanted = bazarr.get(f"{media.plural}/wanted", start=0, length=-1)["data"]
    pairs = [(row, missing["code2"]) for row in wanted for missing in row.get("missing_subtitles") or []
             if missing["code2"] in LANGUAGES and not missing.get("forced") and not missing.get("hi")]
    attempts = state.setdefault("attempts", {})
    still = {f"{media.kind}:{row[media.id_name]}:{language}" for row, language in pairs}
    for key in [key for key in attempts if key.startswith(media.kind + ":") and key not in still]:
        del attempts[key]
    paths: dict[int, str] = {}
    for row, language in pairs:
        media_id = row[media.id_name]
        if media_id not in paths:
            paths[media_id] = bazarr.get(media.plural, **{media.list_query: [media_id]})["data"][0]["path"]
        video = host_path(paths[media_id], data_root)
        try:
            if now - video.stat().st_mtime < GRACE_DAYS * DAY or has_sidecar(video, language):
                continue
        except OSError:
            continue
        record = checked_by_subextract(video, handled)
        source = english_source(video, record) if record else None
        if source:
            yield row, language, video, source


def translate_missing(bazarr, media: Media, data_root: Path, handled: dict, state: dict, now: float,
                      translator, model: str, limit: int, apply: bool, only: str = "", log=print):
    """Translate up to limit wanted languages. Returns (translated, gave up,
    stopped): stopped when Gemini is unavailable (quota, load, network), which counts as no try."""
    attempts = state.setdefault("attempts", {})
    done, gave_up = [], []
    shows: dict[int, dict] = {}
    for row, language, video, source in list(candidates(bazarr, media, data_root, handled, state, now)):
        title = f"{media.title(row)} → {language}"
        if only and only.lower() not in title.lower():
            continue
        key = f"{media.kind}:{row[media.id_name]}:{language}"
        tried = attempts.get(key, {"count": 0, "at": 0})
        if now - tried["at"] < RETRY_SECONDS:
            continue
        if tried["count"] >= MAX_TRIES:
            if not tried.get("reported"):
                gave_up.append(title)
                tried["reported"] = apply
            continue
        if len(done) >= limit:
            break
        log(f"translate: {title} from {source.name}")
        if not apply:
            done.append(title)
            continue
        attempts[key] = {"count": tried["count"] + 1, "at": now}
        show_key = row.get("sonarrSeriesId") or row[media.id_name]
        if show_key not in shows:
            shows[show_key] = media.show(bazarr, row)
        cues = parse_srt(source.read_text(encoding="utf-8-sig", errors="replace"))
        system = instructions(LANGUAGES[language], shows[show_key], media.kind, row.get("episodeTitle", ""),
                              names_to_keep(cues))
        try:
            translated = translate_cues(translator, system, cues)
        except GeminiUnavailable as error:
            attempts[key] = tried
            if not tried["count"]:
                del attempts[key]
            log(f"translate: {error}; the rest next time")
            return done, gave_up, True
        except TranslationFailed as error:
            log(f"translate: {title}: {error}")
            continue
        translated, stopped = shorten(translator, LANGUAGES[language], cues, translated, log)
        destination = video.with_name(f"{video.stem}.{language}.srt")
        del attempts[key]
        if not publish(destination, compose_srt(translated, f"{TAG} ({model})")):
            log(f"translate: {title}: a subtitle appeared meanwhile; kept it, dropped the translation")
            continue
        info = {"kind": media.kind, "id": row[media.id_name], "series": row.get("sonarrSeriesId"),
                "language": language, "title": media.title(row), "at": now}
        state.setdefault("ai", {})["/data/" + str(destination.relative_to(data_root))] = info
        try:
            media.rescan(bazarr, info)
        except (urllib.error.URLError, OSError) as error:
            log(f"translate: Bazarr's rescan failed ({error}); its next scan will see it")
        done.append(title)
        if stopped:
            log("translate: Gemini is unavailable (quota, load); the rest next time")
            return done, gave_up, True
    return done, gave_up, False


def look_for_humans(bazarr, data_root: Path, state: dict, minimum: dict, now: float, limit: int,
                    every_days: float, apply: bool, log=print) -> list[str]:
    """Search again for the AI subtitles searched longest ago; download a
    human one in the same language at Bazarr's minimum score. Forgets AI
    subtitles that are gone or were replaced."""
    ai = state.setdefault("ai", {})
    replaced = []
    for path in list(ai):
        if not is_translation(host_path(path, data_root)):
            log(f"replace: {ai[path]['title']} {ai[path]['language']} is no longer AI-made")
            del ai[path]
    due = sorted((path for path in ai if now - ai[path].get("searched", 0) >= every_days * DAY),
                 key=lambda path: ai[path].get("searched", 0))
    for path in due[:limit]:
        info = ai[path]
        media = KINDS[info["kind"]]
        results = bazarr.get(f"providers/{media.plural}", timeout=300, **{media.search_query: info["id"]})["data"]
        human = [r for r in results if r.get("language") == info["language"] and str(r.get("forced")) == "False"
                 and str(r.get("hearing_impaired")) == "False" and float(r.get("score") or 0) >= minimum[info["kind"]]]
        if apply:
            info["searched"] = now
        if not human:
            continue
        best = max(human, key=lambda r: float(r["score"]))
        label = f"{info['title']} {info['language']} ({best['provider']}, {best['score']}%)"
        log(f"replace: {label}")
        if not apply:
            replaced.append(label)
            continue
        bazarr.request("POST", f"providers/{media.plural}", timeout=300, hi="False", forced="False",
                       original_format="False", provider=best["provider"], subtitle=best["subtitle"],
                       **media.download_query(info))
        if is_translation(host_path(path, data_root)):
            log(f"replace: {path} still has the AI tag after the download")
            continue
        del ai[path]
        replaced.append(label)
    return replaced


def report(translated: list[str], replaced: list[str], gave_up: list[str]) -> None:
    def listed(lines):
        return "\n".join(lines[:15]) + (f"\n… and {len(lines) - 15} more" if len(lines) > 15 else "")
    fields = [(name, listed(lines)) for name, lines in
              (("Translated by AI", translated), ("Replaced by a human subtitle", replaced),
               ("Couldn't translate (3 tries)", gave_up)) if lines]
    if not fields:
        return
    title = "Subtitles: " + ", ".join(f"{len(lines)} {word}" for lines, word in
                                       ((translated, "translated by AI"), (replaced, "replaced"),
                                        (gave_up, "failed")) if lines)
    try:
        apprise.deliver("subtranslate", "downloads", title, apprise.fields_text(fields), "info")
    except apprise.NotSent as error:
        print(f"subtranslate: {error}", file=sys.stderr)


def subextract_lock(data_root: Path, wait_seconds: float):
    """subextract's own lock, waited for up to wait_seconds; None if it stays
    taken."""
    lock_root = data_root / "recycle" / ".subextract"
    lock_root.mkdir(parents=True, exist_ok=True)
    handle = (lock_root / "run.lock").open("a")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if time.monotonic() >= deadline:
                handle.close()
                return None
            time.sleep(15)


def run(args, data_root: Path, env_file: Path, apply: bool) -> int:
    bazarr = Bazarr(args.bazarr_url, env_value(env_file, "BAZARR_API_KEY"))
    key, model = env_value(env_file, "GEMINI_API_KEY"), env_value(env_file, "GEMINI_MODEL")
    translator = Gemini(key, model) if key and model else None
    if not translator:
        print("GEMINI_API_KEY or GEMINI_MODEL isn't set in compose/.env; only looking for human subtitles",
              file=sys.stderr)
    state_path = Path(args.state_dir) / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    try:
        handled = json.loads(Path(args.subextract_state).read_text())
    except (OSError, ValueError) as error:
        print(f"subextract's state can't be read ({error}); not translating", file=sys.stderr)
        handled = {}
    now = time.time()
    translated, replaced, gave_up, failed = [], [], [], 0
    try:
        general = bazarr.get("system/settings")["general"]
        minimum = {kind: float(general[media.score_setting]) for kind, media in KINDS.items()}
        replaced = look_for_humans(bazarr, data_root, state, minimum, now, args.searches, args.research_days, apply)
        left = args.translate if (translator or not apply) else 0
        for media in KINDS.values():
            done, missed, stopped = translate_missing(bazarr, media, data_root, handled, state, now, translator,
                                                      model or "?", left, apply, args.only)
            translated += done
            gave_up += missed
            left = 0 if stopped else left - len(done)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
        print(f"subtranslate: {error}", file=sys.stderr)
        failed = 1
    if translator and translator.requests:
        print(f"subtranslate: {translator.requests} Gemini request(s)")
    if apply:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
        temporary.replace(state_path)
        report(translated, replaced, gave_up)
    return failed


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print what it would do; no Gemini request, no file, no download, no message")
    parser.add_argument("--env-file", default=str(repo / "compose/.env"))
    parser.add_argument("--data-root", default="/mnt/storage/data")
    parser.add_argument("--bazarr-url", default="http://bazarr.homelab.lan")
    parser.add_argument("--translate", type=int, default=6, help="translations per run (Gemini's quota)")
    parser.add_argument("--only", default="", help="translate only titles containing this ('Vinland Saga 2x1 → fr')")
    parser.add_argument("--searches", type=int, default=8, help="AI subtitles searched again per run")
    parser.add_argument("--research-days", type=float, default=7, help="days between searches for one AI subtitle")
    parser.add_argument("--subextract-state", default=str(state_home / "batlab-subextract/handled.json"))
    parser.add_argument("--state-dir", default=str(state_home / "batlab-subtranslate"))
    args = parser.parse_args(argv)
    data_root, apply, env_file = Path(args.data_root), not args.dry_run, Path(args.env_file)
    if not (data_root / "media").is_dir():
        print(f"{data_root}/media is not there; is the data disk mounted?", file=sys.stderr)
        return 1
    lock = subextract_lock(data_root, 30 * 60)
    if lock is None:
        print("subextract has been running for 30 minutes; trying next time", file=sys.stderr)
        return 0
    with lock:
        return run(args, data_root, env_file, apply)


if __name__ == "__main__":
    sys.exit(main())
