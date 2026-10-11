"""Tags: read whatever a file carries, keep an allow-list, check every value.

Anything not on the allow-list (comments, descriptions, URLs, encoder notes,
embedded lyrics, private frames) is never copied. What is copied is text that
has been normalised, stripped of control and direction-override characters,
cut to a sane length, and checked against what that field may hold.
"""

import base64
import re
import unicodedata

import mutagen
from mutagen.easyid3 import EasyID3
from mutagen.easymp4 import EasyMP4Tags
from mutagen.flac import Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4, MP4Cover

# Canonical key -> spellings seen in the wild (lower case, spaces and dashes
# as underscores). ffprobe, Picard, beets, yt-dlp and Lidarr disagree on most.
ALIASES = {
    "title": ["title"],
    "artist": ["artist"],
    "artists": ["artists"],
    "album": ["album"],
    "albumartist": ["albumartist", "album_artist"],
    "date": ["date", "year", "tdrc", "tyer"],
    "originaldate": ["originaldate", "original_date", "originalyear", "tory", "tdor"],
    "tracknumber": ["tracknumber", "track"],
    "discnumber": ["discnumber", "disc"],
    "tracktotal": ["tracktotal", "totaltracks"],
    "disctotal": ["disctotal", "totaldiscs"],
    "genre": ["genre"],
    "composer": ["composer"],
    "isrc": ["isrc", "tsrc"],
    "barcode": ["barcode"],
    "media": ["media", "tmed"],
    "compilation": ["compilation", "tcmp"],
    "artistsort": ["artistsort", "artist_sort", "tsop"],
    "albumartistsort": ["albumartistsort", "album_artist_sort", "tso2"],
    "musicbrainz_trackid": ["musicbrainz_trackid", "musicbrainz_track_id"],
    "musicbrainz_releasetrackid": ["musicbrainz_releasetrackid", "musicbrainz_release_track_id"],
    "musicbrainz_albumid": ["musicbrainz_albumid", "musicbrainz_album_id"],
    "musicbrainz_artistid": ["musicbrainz_artistid", "musicbrainz_artist_id"],
    "musicbrainz_albumartistid": ["musicbrainz_albumartistid", "musicbrainz_album_artist_id"],
    "musicbrainz_releasegroupid": ["musicbrainz_releasegroupid", "musicbrainz_release_group_id"],
    "musicbrainz_albumtype": ["musicbrainz_albumtype", "musicbrainz_album_type", "releasetype"],
    "musicbrainz_albumstatus": ["musicbrainz_albumstatus", "musicbrainz_album_status", "releasestatus"],
    "releasecountry": ["releasecountry", "musicbrainz_album_release_country"],
    "acoustid_id": ["acoustid_id"],
    # Aurral's own marker for the files it downloaded; dropping it would make
    # Aurral lose track of them.
    "aurral_ids": ["aurral_ids"],
}
LOOKUP = {spelling: key for key, spellings in ALIASES.items() for spelling in spellings}

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UUID_KEYS = {"musicbrainz_trackid", "musicbrainz_releasetrackid", "musicbrainz_albumid",
             "musicbrainz_artistid", "musicbrainz_albumartistid", "musicbrainz_releasegroupid",
             "acoustid_id"}
NUMBER = re.compile(r"^\s*(\d{1,3})\s*(?:/\s*(\d{1,3})\s*)?$")
DATE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
COMPACT_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
ISRC = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
SUSPICIOUS = re.compile(r"(?i)<\s*/?\s*(?:script|iframe|object|embed|svg|img|a)\b|javascript:|"
                        r"<\?php|\$\{|\{\{|%\{|\bon\w+\s*=")
# Direction overrides and isolates reorder what is displayed ("Trojan Source").
BIDI_CONTROLS = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}
KEEP_FORMAT = {"‌", "‍"}  # joiners shape Arabic and emoji

MAX_TEXT = 300
MAX_VALUES = 16
MAX_AURRAL = 4096


def normalise_key(key):
    key = key.lower().strip()
    if key.startswith("txxx:"):
        key = key[5:]
    if key.startswith("----:com.apple.itunes:"):
        key = key[len("----:com.apple.itunes:"):]
    return re.sub(r"[\s\-]+", "_", key)


def clean_text(value, findings):
    if not isinstance(value, str):
        value = str(value)
    value = unicodedata.normalize("NFC", value)
    kept = []
    for char in value:
        if char in BIDI_CONTROLS:
            continue
        category = unicodedata.category(char)
        if category == "Cc" or (category == "Cf" and char not in KEEP_FORMAT):
            kept.append(" ")
            continue
        kept.append(char)
    value = "".join(kept)
    if SUSPICIOUS.search(value):
        findings.append(f"markup or template in a tag: {value[:80]!r}")
        return ""
    if URL.search(value):
        findings.append(f"URL removed from a tag: {value[:80]!r}")
        value = URL.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" -|/")
    return value[:MAX_TEXT].strip()


def split_values(key, raw):
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    out = []
    for value in values:
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        value = str(value)
        if key in UUID_KEYS or key == "artists":
            # ID3v2.3 joins several values into one with "/" or ";".
            out.extend(part for part in re.split(r"\s*[/;]\s*", value) if part)
        else:
            out.append(value)
    return out


def check_value(key, value, findings):
    """The value as it may be stored, or None to drop it."""
    if key == "aurral_ids":
        value = "".join(c for c in str(value) if c.isprintable())
        return value[:MAX_AURRAL] or None
    value = clean_text(value, findings)
    if not value:
        return None
    if key in UUID_KEYS:
        value = value.lower()
        return value if UUID.match(value) else None
    if key in ("tracknumber", "discnumber"):
        match = NUMBER.match(value)
        if not match or int(match.group(1)) == 0:
            return None
        return f"{int(match.group(1))}/{int(match.group(2))}" if match.group(2) else str(int(match.group(1)))
    if key in ("tracktotal", "disctotal"):
        return str(int(value)) if value.isdigit() and 0 < int(value) < 1000 else None
    if key in ("date", "originaldate"):
        compact = COMPACT_DATE.match(value)
        if compact:
            value = f"{compact.group(1)}-{compact.group(2)}-{compact.group(3)}"
        match = DATE.match(value)
        if not match or not 1900 <= int(match.group(1)) <= 2100:
            return None
        return match.group(0)
    if key == "isrc":
        value = value.replace("-", "").upper()
        return value if ISRC.match(value) else None
    if key == "barcode":
        return value if value.isdigit() and 8 <= len(value) <= 14 else None
    if key == "compilation":
        return "1" if value in ("1", "true", "yes") else None
    return value


def collect(raw_items):
    """Map (key, value) pairs from any reader onto the allow-list.

    Returns (tags, findings): tags maps canonical keys to lists of checked
    values; findings lists what was removed for a reason worth reporting.
    """
    tags, findings = {}, []
    for raw_key, raw_value in raw_items:
        key = LOOKUP.get(normalise_key(str(raw_key)))
        if key is None:
            continue
        for value in split_values(key, raw_value):
            checked = check_value(key, value, findings)
            if checked is None:
                continue
            values = tags.setdefault(key, [])
            if checked not in values and len(values) < MAX_VALUES:
                values.append(checked)
    for number, total in (("tracknumber", "tracktotal"), ("discnumber", "disctotal")):
        if number in tags and total in tags and "/" not in tags[number][0]:
            tags[number] = [f"{tags[number][0]}/{tags[total][0]}"]
        tags.pop(total, None)
    for single in ("title", "album", "albumartist", "date", "originaldate", "tracknumber",
                   "discnumber", "musicbrainz_albumid", "musicbrainz_releasegroupid",
                   "musicbrainz_trackid", "musicbrainz_releasetrackid", "compilation", "aurral_ids"):
        if single in tags:
            tags[single] = tags[single][:1]
    return tags, findings


def read_mutagen(path):
    """(key, value) pairs through mutagen's normalising readers, or None."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:  # mutagen raises a dozen types on hostile input
        return None
    if audio is None or audio.tags is None:
        return None if audio is None else []
    items = []
    try:
        for key in audio.tags.keys():
            items.append((key, audio.tags[key]))
    except Exception:
        return None
    if path.lower().endswith(".mp3"):
        # EasyID3 hides TXXX frames it has no name for; Picard keeps several
        # MusicBrainz IDs and Aurral its marker there.
        try:
            for frame in ID3(path).getall("TXXX"):
                items.append((frame.desc, list(frame.text)))
        except Exception:
            pass
    return items


def read_ffprobe(info):
    items = list(info.get("format", {}).get("tags", {}).items())
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            items.extend(stream.get("tags", {}).items())
    return items


DECORATION = re.compile(
    r"(?i)\s*[\(\[\{][^\)\]\}]*\b(?:official|officiel|video|vid[eé]o|clip|audio|lyrics?|paroles|"
    r"visuali[sz]er|hd|hq|4k|explicit|music\s+video|no\s+copyright|copyright\s+free|"
    r"royalty\s+free|free\s+download|background\s+music|ncs\s+release)\b[^\)\]\}]*[\)\]\}]")
# Channel names around the artist's: "Drake - Topic", "DrakeVEVO", "Drake Archive".
CHANNEL_NOISE = re.compile(r"(?i)(?:\s+-\s+topic|\s*vevo|\s+(?:official|archive|music|tv))$")
DASH = re.compile(r"\s+[-\u2013\u2014]\s+")  # "Artist - Song", with a hyphen or a dash
COLON = re.compile(r"\s*[:\uff1a]\s+")      # "Artist: Song"


def comparable(text):
    return re.sub(r"\W+", "", text.casefold())


def similar(a, b):
    a, b = comparable(a), comparable(b)
    return bool(a and b) and (a in b or b in a)


def fill_from_link(tags, artist_side=None):
    """Best effort for files fetched from a video link, which carry the video's
    title and the channel's name rather than music tags.

    A file with an album came from YouTube Music's own data and is left alone.
    Otherwise "X - Y" is split when the channel names one side; when it names
    neither, artist_side(left, right) may decide ("left", "right" or None).
    """
    for key in ("artist", "albumartist"):
        if key in tags:
            tags[key] = [CHANNEL_NOISE.sub("", value).strip() for value in tags[key]]
    title = DECORATION.sub("", tags.get("title", [""])[0]).strip()
    channel = tags.get("artist", [""])[0]
    artist = channel
    if "album" not in tags:
        dash = DASH.split(title, 1)
        colon = COLON.split(title, 1)
        if len(dash) == 2 and all(p.strip() for p in dash):
            left, right = (p.strip() for p in dash)
            if not channel or similar(channel, left):
                artist, title = left, right
            elif similar(channel, right):
                artist, title = right, left
            elif artist_side:
                side = artist_side(left, right)
                if side == "left":
                    artist, title = left, right
                elif side == "right":
                    artist, title = right, left
        elif len(colon) == 2 and all(p.strip() for p in colon) and channel and similar(channel, colon[0]):
            artist, title = colon[0].strip(), colon[1].strip()
    if title:
        tags["title"] = [title]
    if artist:
        tags["artist"] = [artist]
        if "albumartist" in tags and tags["albumartist"][0] == channel:
            tags["albumartist"] = [artist]
    return tags


def complete(tags, fallback_title):
    """Fields every file in the library must have, so it files and sorts."""
    if not tags.get("title"):
        tags["title"] = [fallback_title or "Unknown Title"]
    if not tags.get("artist"):
        tags["artist"] = tags.get("albumartist") or ["Unknown Artist"]
    if not tags.get("albumartist"):
        tags["albumartist"] = tags["artist"][:1]
    if not tags.get("album"):
        tags["album"] = tags["title"][:1]
        tags.setdefault("tracknumber", ["1"])
    return tags


# --- writing -----------------------------------------------------------------

ID3_EXTRA = {"artists": "ARTISTS", "aurral_ids": "AURRAL_IDS"}
MP4_EXTRA = {"artists": "ARTISTS", "aurral_ids": "AURRAL_IDS", "isrc": "ISRC",
             "barcode": "BARCODE", "media": "MEDIA", "originaldate": "ORIGINALDATE",
             "acoustid_id": "Acoustid Id",
             "musicbrainz_releasegroupid": "MusicBrainz Release Group Id",
             "musicbrainz_releasetrackid": "MusicBrainz Release Track Id"}
for _key, _desc in ID3_EXTRA.items():
    if _key not in EasyID3.valid_keys:
        EasyID3.RegisterTXXXKey(_key, _desc)
for _key, _desc in MP4_EXTRA.items():
    if _key not in EasyMP4Tags.Get:
        EasyMP4Tags.RegisterFreeformKey(_key, _desc)


def write(path, tags, cover_jpeg=None):
    """Write tags (and a cover) into a rebuilt file that carries none."""
    audio = mutagen.File(path, easy=True)
    if audio is None:
        raise ValueError("mutagen cannot open the rebuilt file")
    if audio.tags is None:
        audio.add_tags()
    tags = dict(tags)
    if not path.endswith((".mp3", ".m4a")):
        # Vorbis comments keep the total in its own field: TRACKNUMBER=3,
        # TRACKTOTAL=12, not "3/12".
        for key, total in (("tracknumber", "tracktotal"), ("discnumber", "disctotal")):
            if key in tags and "/" in tags[key][0]:
                n, of = tags[key][0].split("/", 1)
                tags[key], tags[total] = [n], [of]
    skipped = []
    for key, values in tags.items():
        try:
            audio.tags[key] = values
        except (KeyError, ValueError):
            skipped.append(key)
    if path.endswith(".mp3"):
        audio.save(v2_version=4)
    else:
        audio.save()
    if cover_jpeg:
        embed_cover(path, cover_jpeg)
    return skipped


def embed_cover(path, jpeg):
    if path.endswith(".mp3"):
        id3 = ID3(path)
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=jpeg))
        id3.save(v2_version=4)
        return
    if path.endswith(".m4a"):
        mp4 = MP4(path)
        mp4["covr"] = [MP4Cover(jpeg, imageformat=MP4Cover.FORMAT_JPEG)]
        mp4.save()
        return
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.desc = "Cover"
    picture.data = jpeg
    audio = mutagen.File(path)
    if path.endswith(".flac"):
        audio.add_picture(picture)
    else:  # Ogg Opus and Vorbis carry pictures as a base64 comment
        audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
    audio.save()
