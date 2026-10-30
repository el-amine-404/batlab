"""Lyrics files beside a song, in every form players read, and the marks
Stanzarr leaves in them.

Sidecars share the song's name: .lrc (synced), .elrc (word-synced),
.txt (plain), .ttml (Apple's timed XML) and .yaml / .yml (Navidrome's
Lyricsfile). Lyrics can also be embedded in the audio file's tags.

An .lrc says where it came from in its info tags, which players ignore:

  [by:AI-generated, not checked by a person]     synced by AI, nobody looked
  [by:AI-generated, checked by a person]         a person approved it as is
  [by:AI-generated, corrected by a person]       a person fixed it
  [re:LRCLIB via Stanzarr]                       from LRCLIB (synced by a person)

Anything flagged AI-generated stays replaceable: a human-synced version from
LRCLIB takes its place when one appears.
"""

import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field

import mutagen

from . import language

SIDECARS = {".lrc": "lrc", ".elrc": "elrc", ".txt": "txt", ".ttml": "ttml", ".yaml": "yaml", ".yml": "yaml"}
AUDIO = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".alac", ".opus", ".ogg", ".oga", ".wav", ".aif", ".aiff",
         ".wma", ".ape", ".wv", ".dsf"}
AI_MARK = "[by:AI-generated"
MAX_BYTES = 512 * 1024
STAMP = re.compile(r"\[(\d{1,3}):(\d{2}(?:[.:]\d{1,3})?)\]")
TAG = re.compile(r"^\[([a-z#]+):([^\]]*)\]\s*$", re.IGNORECASE)
NUMBERS = re.compile(r"\d+")
# Cyrillic and Greek letters drawn like Latin ones; inside a Latin word they
# are a typing or copying accident that reads the same but defeats search.
LOOKALIKES = str.maketrans("аеорсухіјѕАВЕКМНОРСТХІЈЅοΑΒΕΗΙΚΜΝΟΡΤΧΥΖ", "aeopcyxijsABEKMHOPCTXIJSoABEHIKMNOPTXYZ")
WORD = re.compile(r"\w+")

# Word stamps inside a line (<01:02.34>): enhanced LRC, words in time.
WORD_STAMP = re.compile(r"<\d{1,3}:\d{2}[.:]\d{1,3}>")

# What no lyric needs, and what some use to hide or turn text around:
# control characters, the bidirectional overrides and isolates, zero widths,
# the byte order mark.
HIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
HTML = re.compile(r"<\s*/?\s*(script|style|iframe|object|embed|a|div|span|p|br|img|svg|meta|link)\b[^>]*>", re.IGNORECASE)
# The lines lyrics sites add that aren't lyrics: credits to the site, links, ads.
ADS = re.compile(r"(?i)(lyrics (provided|powered|licensed) by|paroles de la chanson|\bwww\.|https?://|"
                 r"you might also like|\d*embed$|get tickets|see .{1,40} live)")
MAX_LINE = 300
MAX_LINES = 1500


@dataclass
class Checked:
    """What sanitize() made of a text: the text to write, what it took out,
    and why it refused the whole of it (None: it didn't)."""
    text: str
    removed: list[str] = field(default_factory=list)
    refused: str | None = None


def sanitize(text: str, duration: float = 0.0) -> Checked:
    """Lyrics from a source, made safe to write beside a song and to show:
    no binary, no hidden or direction-turning characters, no HTML, no site
    credits or links, no line a player would choke on, no time past the song's
    end. Refused whole when it can't be lyrics."""
    if "\x00" in text:
        return Checked("", refused="it holds binary data")
    if len(text.encode("utf-8", "replace")) > MAX_BYTES:
        return Checked("", refused=f"it is over {MAX_BYTES // 1024} KB")
    removed: list[str] = []
    hidden = len(HIDDEN.findall(text))
    if hidden:
        text = HIDDEN.sub("", text)
        removed.append(f"{hidden} hidden or control characters")
    if HTML.search(text):
        text = HTML.sub("", text)
        removed.append("HTML tags")
    kept = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = WORD_STAMP.sub("", STAMP.sub("", line)).strip()
        if words and len(words) < 200 and ADS.search(words):
            removed.append(f"not lyrics: {words[:80]}")
            continue
        if len(words) > MAX_LINE:
            removed.append(f"a line of {len(words)} characters")
            continue
        stamp = STAMP.match(line.strip())
        if duration and stamp:
            minutes, seconds = stamp.groups()
            at = int(minutes) * 60 + float(seconds.replace(":", "."))
            if at > duration + 10:
                removed.append(f"a line timed after the song's end ({stamp.group(0)})")
                continue
        kept.append(line)
    if sum(1 for line in kept if line.strip()) > MAX_LINES:
        return Checked("", removed, refused=f"over {MAX_LINES} lines")
    out = "\n".join(kept).strip("\n")
    if not out.strip():
        return Checked("", removed, refused="nothing left once cleaned")
    return Checked(out + "\n", removed)


def word_synced(text: str) -> bool:
    """Words in time (enhanced LRC), not only lines."""
    return bool(WORD_STAMP.search(text))


@dataclass
class Line:
    t: float | None
    s: str


@dataclass
class Lyrics:
    lines: list[Line] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def synced(self) -> bool:
        return any(line.t is not None for line in self.lines)

    @property
    def text(self) -> str:
        return "\n".join(line.s for line in self.lines if line.s.strip())


def _latinise(match: re.Match) -> str:
    word = match.group(0)
    return word.translate(LOOKALIKES) if any("a" <= c.lower() <= "z" for c in word) else word


def clean(text: str) -> str:
    """Plain text without control characters or look-alike letters inside Latin words."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    out = []
    for line in text.split("\n")[:5000]:
        line = "".join(c for c in line if c == "\t" or unicodedata.category(c) != "Cc")
        out.append(WORD.sub(_latinise, line).rstrip()[:500])
    return "\n".join(out).strip() + "\n"


def parse_lrc(text: str) -> Lyrics:
    result = Lyrics()
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        tag = TAG.match(raw)
        if tag and not STAMP.match(raw):
            key = tag.group(1).lower()
            result.tags[key] = (result.tags[key] + "\n" if key in result.tags else "") + tag.group(2).strip()
            continue
        stamps = STAMP.findall(raw)
        words = STAMP.sub("", raw).strip()
        words = re.sub(r"<\d{1,3}:\d{2}(?:[.:]\d{1,3})?>", "", words).strip()  # word stamps (.elrc)
        if not stamps:
            result.lines.append(Line(None, words))
        for minutes, seconds in stamps:  # "[00:10.00][00:40.00] chorus" repeats a line
            result.lines.append(Line(int(minutes) * 60 + float(seconds.replace(":", ".")), words))
    if result.synced:
        result.lines = sorted((l for l in result.lines if l.t is not None), key=lambda l: l.t)
    return result


def parse_plain(text: str) -> Lyrics:
    return Lyrics([Line(None, line.strip()) for line in text.splitlines() if line.strip()])


def parse_ttml(text: str) -> Lyrics:
    lang = re.search(r'xml:lang="([^"]+)"', text)
    lines = []
    for attributes, body in re.findall(r"<p\b([^>]*)>(.*?)</p>", text, re.S):
        words = re.sub(r"<[^>]+>", "", body).strip()
        found = re.search(r'begin="([^"]+)"', attributes)
        begin = found.group(1) if found else ""
        t = None
        if begin:
            parts = [float(p) for p in re.split(r"[:]", begin.rstrip("s")) if p]
            t = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
        lines.append(Line(t, words))
    return Lyrics(lines, {"la": lang.group(1)} if lang else {})


def parse_yaml(text: str) -> Lyrics:
    """Navidrome's Lyricsfile, read loosely: lines with a time and text."""
    lines = []
    for m in re.finditer(r"(?:time|start)\s*:\s*([\d.:]+)[^\n]*\n\s*(?:-\s*)?(?:text|value|line)\s*:\s*(.+)", text):
        raw = m.group(1)
        parts = [float(p) for p in raw.split(":") if p]
        t = sum(v * 60 ** i for i, v in enumerate(reversed(parts)))
        lines.append(Line(t / 1000 if t > 36000 else t, m.group(2).strip().strip("'\"")))
    if not lines:
        lines = [Line(None, m.group(1).strip().strip("'\"")) for m in re.finditer(r"(?:text|value|line)\s*:\s*(.+)", text)]
    return Lyrics(lines)


def read_sidecar(path: str, fmt: str) -> Lyrics:
    with open(path, "rb") as handle:
        data = handle.read(MAX_BYTES)
    text = data.decode("utf-8", errors="replace")
    if fmt in ("lrc", "elrc"):
        return parse_lrc(text)
    if fmt == "ttml":
        return parse_ttml(text)
    if fmt == "yaml":
        return parse_yaml(text)
    return parse_plain(text)


def read_embedded(audio_path: str) -> Lyrics | None:
    try:
        audio = mutagen.File(audio_path)
    except Exception:
        return None
    if audio is None or audio.tags is None:
        return None
    tags = audio.tags
    texts: list[str] = []
    getall = getattr(tags, "getall", None)
    if getall:  # ID3
        for frame in getall("SYLT"):
            return Lyrics([Line(ms / 1000, str(words).strip()) for words, ms in frame.text if str(words).strip()])
        texts = [str(frame.text) for frame in getall("USLT")]
    else:
        for key in ("lyrics", "LYRICS", "unsyncedlyrics", "UNSYNCEDLYRICS", "\xa9lyr"):
            try:
                value = tags.get(key)
            except (KeyError, ValueError):
                value = None
            if value:
                texts = [str(v) for v in (value if isinstance(value, list) else [value])]
                break
    text = "\n".join(t for t in texts if t.strip())
    if not text.strip():
        return None
    return parse_lrc(text) if STAMP.search(text) else parse_plain(text)


@dataclass
class Origin:
    source: str  # lrclib | netease | musixmatch | genius | ai | person | unknown
    ai_state: str | None  # unchecked | checked | corrected (AI files only)
    unsure: int
    checked: int


def _numbers(value: str) -> int:
    return 0 if not value or value.strip().lower() == "none" else len(NUMBERS.findall(value))


def origin(lyrics: Lyrics) -> Origin:
    by = lyrics.tags.get("by", "")
    re_ = lyrics.tags.get("re", "")
    notes = lyrics.tags.get("#", "")
    unsure = checked = 0
    for note in notes.split("\n"):
        key, _, value = note.partition(":")
        if key.strip().lower().startswith("uncertain lines"):
            unsure = _numbers(value)
        elif key.strip().lower().startswith("checked lines"):
            checked = _numbers(value)
    if by.startswith("AI-generated"):
        state = "corrected" if "corrected" in by else "checked" if "checked by a person" in by and "not" not in by else "unchecked"
        return Origin("ai", state, unsure, checked)
    if "user_uploaded" in by.lower() or "user uploaded" in by.lower() or "user_uploaded" in re_.lower() or "user uploaded" in re_.lower():
        return Origin("user_uploaded", None, 0, 0)
    # A source's file says so: [re:<its name> via Stanzarr].
    for mark, source in (("lrclib", "lrclib"), ("netease", "netease"), ("musixmatch", "musixmatch"), ("genius", "genius"),
                         ("lyrics.ovh", "lyricsovh")):
        if mark in re_.lower():
            return Origin(source, None, 0, 0)
    if "stanzarr" in re_.lower() or "a person" in by:
        return Origin("person", None, 0, 0)
    return Origin("unknown", None, 0, 0)


def is_ai(path: str) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return AI_MARK in handle.read(4096)
    except OSError:
        return False


def detect_language(lyrics: Lyrics) -> str:
    return language.detect(lyrics.text, lyrics.tags.get("la", ""))


def stamp(t: float) -> str:
    t = max(0.0, t)
    return f"[{int(t // 60):02d}:{t % 60:06.3f}]"


def render_lrc(lines: list[Line], tags: list[str]) -> str:
    body = [f"{stamp(l.t)} {l.s}" for l in sorted(lines, key=lambda l: l.t or 0) if l.t is not None and l.s.strip()]
    return "\n".join(tags + body) + "\n"


def write_atomic(path: str, text: str) -> None:
    """Write beside the target and rename over it: nothing ever sees half a file."""
    directory = os.path.dirname(path)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".stanzarr-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
