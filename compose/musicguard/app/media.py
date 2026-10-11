"""ffprobe and ffmpeg: what a file holds, and rebuilding it as audio alone.

Every call runs with a timeout and no stdin, on paths that never reach a shell.
"""

import json
import os
import re
import subprocess

FFPROBE_TIMEOUT = 60
FFMPEG_TIMEOUT = 900

# Output container for each audio codec. A copy keeps the audio bit for bit;
# PCM is the one codec re-encoded, losslessly, because a WAV carries no tags.
CONTAINERS = {
    "mp3": ("mp3", ".mp3", ["-c:a", "copy", "-write_xing", "1", "-id3v2_version", "4"]),
    "flac": ("flac", ".flac", ["-c:a", "copy"]),
    "aac": ("ipod", ".m4a", ["-c:a", "copy", "-movflags", "+faststart"]),
    "alac": ("ipod", ".m4a", ["-c:a", "copy", "-movflags", "+faststart"]),
    "opus": ("opus", ".opus", ["-c:a", "copy"]),
    "vorbis": ("ogg", ".ogg", ["-c:a", "copy"]),
}
PCM = re.compile(r"^pcm_(s16|s24|s32|f32)(le|be)$")
PCM_TARGET = ("flac", ".flac", ["-c:a", "flac", "-compression_level", "8"])

LOSSLESS = {"flac", "alac"}

# What a file starts with when it is a program or an archive rather than audio.
DANGEROUS_MAGIC = [
    (b"MZ", "Windows executable"),
    (b"\x7fELF", "Linux executable"),
    (b"#!", "script"),
    (b"PK\x03\x04", "zip archive"),
    (b"Rar!", "rar archive"),
    (b"7z\xbc\xaf\x27\x1c", "7z archive"),
    (b"\xca\xfe\xba\xbe", "Mach-O or Java class"),
    (b"\xcf\xfa\xed\xfe", "Mach-O executable"),
    (b"%PDF", "PDF"),
    (b"<?php", "PHP script"),
    (b"{\\rtf", "RTF document"),
    (b"\xd0\xcf\x11\xe0", "Office document"),
]
TEXT_PROBES = (b"<script", b"<html", b"<!doctype", b"powershell", b"#!/")


class MediaError(Exception):
    """A file ffmpeg cannot read, or that is not what a music file must be."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def run(command, timeout):
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=timeout, check=False)


def sniff(path):
    """The kind of program or archive a file is, or None for anything else."""
    with open(path, "rb") as handle:
        head = handle.read(4096)
    for magic, kind in DANGEROUS_MAGIC:
        if head.startswith(magic):
            return kind
    lowered = head.lower()
    # ID3 and FLAC headers are binary; markup in the first block is not music.
    if not head.startswith((b"ID3", b"fLaC", b"OggS")) and any(p in lowered for p in TEXT_PROBES):
        return "markup or script"
    return None


def probe(path):
    result = run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                  "-of", "json", "--", path], FFPROBE_TIMEOUT)
    if result.returncode != 0:
        raise MediaError("NOT_AUDIO", result.stderr.decode(errors="replace").strip()[:300])
    try:
        info = json.loads(result.stdout)
    except ValueError as error:
        raise MediaError("NOT_AUDIO", f"unreadable ffprobe output: {error}") from error
    return info


# What the content must be for each name. A file whose bytes say one format
# while its name says another is a disguise or a polyglot, never a download.
FORMATS = {
    ".mp3": {"mp3"},
    ".flac": {"flac"},
    ".m4a": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".mp4": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".alac": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".aac": {"aac"},
    ".opus": {"ogg"},
    ".ogg": {"ogg"},
    ".oga": {"ogg"},
    ".webm": {"matroska", "webm"},
    ".mka": {"matroska", "webm"},
    ".wav": {"wav"},
    ".aif": {"aiff"},
    ".aiff": {"aiff"},
}


def check_format(path, info):
    extension = os.path.splitext(path)[1].lower()
    detected = set(info.get("format", {}).get("format_name", "").split(","))
    allowed = FORMATS.get(extension)
    if allowed is None or not detected & allowed:
        raise MediaError("MISMATCH", f"content is {','.join(sorted(detected)) or 'unknown'}, "
                                     f"name says {extension or 'nothing'}")


def audio_stream(info):
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    raise MediaError("NOT_AUDIO", "no audio stream")


def cover_stream(info):
    """The index of embedded cover art: a still picture, never a video track."""
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("disposition", {}).get("attached_pic") == 1:
            return stream["index"]
    return None


def duration(info):
    for source in (info.get("format", {}), audio_stream(info)):
        try:
            return float(source.get("duration"))
        except (TypeError, ValueError):
            continue
    # ffprobe falls back on the name and reports e.g. "flac" for random bytes
    # called .flac, but finds no length in them.
    raise MediaError("NOT_AUDIO", "no measurable length")


def target_for(codec):
    if codec in CONTAINERS:
        return CONTAINERS[codec]
    if PCM.match(codec or ""):
        return PCM_TARGET
    raise MediaError("UNSUPPORTED_CODEC", codec or "unknown")


def decode_errors(path, stream_index):
    """Decode the whole audio stream; return ffmpeg's complaints, if any."""
    result = run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-map", f"0:{stream_index}",
                  "-f", "null", "-"], FFMPEG_TIMEOUT)
    lines = [line for line in result.stderr.decode(errors="replace").splitlines() if line.strip()]
    if result.returncode != 0:
        raise MediaError("CORRUPT", (lines[-1] if lines else f"ffmpeg exited {result.returncode}")[:300])
    return lines


def rebuild(source, stream_index, codec, destination):
    """Write the audio stream alone into a fresh container.

    Tags, pictures, chapters, other streams and anything appended after the
    audio are left behind: only the audio packets are copied, and the
    container around them is new.
    """
    muxer, _, codec_args = target_for(codec)
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source,
               "-map", f"0:{stream_index}", *codec_args,
               "-map_metadata", "-1", "-map_chapters", "-1",
               "-fflags", "+bitexact", "-flags:a", "+bitexact",
               "-f", muxer, destination]
    result = run(command, FFMPEG_TIMEOUT)
    if result.returncode != 0:
        raise MediaError("REBUILD_FAILED", result.stderr.decode(errors="replace").strip()[-300:])


def extract_cover(source, stream_index, destination, square=False):
    """Decode a picture and encode it again as a plain JPEG of at most 1200 px.

    Decoding and re-encoding keeps the pixels and drops everything else the
    original carried: comments, profiles, thumbnails, bytes after the end
    marker. A picture ffmpeg cannot decode is dropped.
    """
    filters = []
    if square:
        filters.append("crop='min(iw,ih)':'min(iw,ih)'")
    filters.append("scale='min(1200,iw)':'min(1200,ih)':force_original_aspect_ratio=decrease")
    filters.append("format=yuvj420p")
    map_args = ["-map", f"0:{stream_index}"] if stream_index is not None else []
    result = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, *map_args,
                  "-frames:v", "1", "-vf", ",".join(filters), "-map_metadata", "-1",
                  "-fflags", "+bitexact", "-c:v", "mjpeg", "-q:v", "3", "-f", "image2", destination],
                 120)
    if result.returncode != 0 or not os.path.exists(destination) or os.path.getsize(destination) < 100:
        if os.path.exists(destination):
            os.unlink(destination)
        return False
    return True


def image_end(data):
    """Where a JPEG or PNG really ends, by walking its structure; None when it
    is neither, or is cut short. Bytes past this point were appended."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        position = 8
        while position + 12 <= len(data):
            length = int.from_bytes(data[position:position + 4], "big")
            kind = data[position + 4:position + 8]
            position += 12 + length
            if kind == b"IEND":
                return position if position <= len(data) else None
        return None
    if data.startswith(b"\xff\xd8"):
        position = 2
        while position + 2 <= len(data):
            if data[position] != 0xFF:
                return None
            marker = data[position + 1]
            if marker == 0xD9:
                return position + 2
            if 0xD0 <= marker <= 0xD7 or marker == 0x01:
                position += 2
                continue
            if position + 4 > len(data):
                return None
            length = int.from_bytes(data[position + 2:position + 4], "big")
            position += 2 + length
            if marker == 0xDA:
                # Entropy-coded data: FF is followed by 00 (a stuffed byte) or a
                # restart marker; anything else ends the scan.
                while position + 1 < len(data):
                    if data[position] == 0xFF and data[position + 1] not in (0x00, *range(0xD0, 0xD8)):
                        break
                    position += 1
        return None
    return None


def check_cover(source, stream_index, workdir):
    """A problem with the embedded picture as it is, or None when it is a
    plain image that decodes and ends where its format says it ends."""
    raw = os.path.join(workdir, "raw-cover")
    result = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-map", f"0:{stream_index}",
                  "-c", "copy", "-frames:v", "1", "-f", "image2", raw], 120)
    if result.returncode != 0 or not os.path.exists(raw):
        return "NOT_AN_IMAGE", "the picture cannot be extracted"
    with open(raw, "rb") as handle:
        data = handle.read()
    end = image_end(data)
    if end is None:
        return "NOT_AN_IMAGE", "not a complete JPEG or PNG"
    if len(data) - end > 16 or data[end:].strip(b"\x00"):
        return "APPENDED_DATA", f"{len(data) - end} bytes after the end of the picture"
    decoded = run(["ffmpeg", "-nostdin", "-v", "error", "-i", raw, "-f", "null", "-"], 120)
    if decoded.returncode != 0:
        return "NOT_AN_IMAGE", "the picture does not decode"
    return None


RMS = re.compile(r"RMS level dB:\s*(-?inf|-?[0-9.]+)")


def band_level(path, stream_index, start, length, cutoff):
    """RMS level in dB of everything above cutoff Hz (0: the whole signal)."""
    filters = ["aformat=channel_layouts=mono"]
    if cutoff:
        keep = f"if(lt(b*sr/(2*nb),{cutoff}),0,re)"
        keep_im = f"if(lt(b*sr/(2*nb),{cutoff}),0,im)"
        filters.append(f"afftfilt=real='{keep}':imag='{keep_im}':win_size=4096")
    filters.append("astats=measure_perchannel=none:measure_overall=RMS_level")
    result = run(["ffmpeg", "-nostdin", "-v", "info", "-ss", f"{start:.1f}", "-t", f"{length:.1f}",
                  "-i", path, "-map", f"0:{stream_index}", "-af", ",".join(filters),
                  "-f", "null", "-"], 300)
    match = RMS.search(result.stderr.decode(errors="replace"))
    if not match:
        return None
    value = match.group(1)
    return float("-inf") if value.endswith("inf") else float(value)


# Levels above each cutoff, in dB. Between two neighbouring cutoffs, a fall of
# CLIFF_DB or more, ending well below the whole signal, is an encoder's lowpass:
# music fades towards the top of the spectrum, it does not stop.
CUTOFFS = (12000, 16000, 19000, 20500)
CLIFF_DB = 25.0
EMPTY_BELOW_TOTAL_DB = -50.0


def spectrum_verdict(path, stream_index, total_seconds, sample_rate):
    """Whether a lossless or high-bitrate file was made from a lossy one.

    MP3 encoders cut everything above a bitrate-dependent frequency (about
    16-17 kHz at 128k, 20 kHz at 320k) and nothing brings it back, so a FLAC
    with an empty top band was decoded from a lossy file. Old recordings can
    stop early too, so this is a report, never a reason to reject.
    Returns (verdict, levels) with verdict None when the file looks genuine.
    """
    if sample_rate < 44100 or total_seconds < 20:
        return None, {}
    length = min(30.0, total_seconds - 2)
    start = max(0.0, total_seconds / 2 - length / 2)
    total = band_level(path, stream_index, start, length, 0)
    if total is None or total < -60:
        return None, {}
    levels = {"total": round(total, 1)}
    previous = None
    for cutoff in CUTOFFS:
        level = band_level(path, stream_index, start, length, cutoff)
        if level is None:
            return None, levels
        level = max(level, -200.0)
        levels[str(cutoff)] = round(level, 1)
        if previous is not None and previous - level >= CLIFF_DB \
                and level - total <= EMPTY_BELOW_TOTAL_DB:
            return ("LOSSY_SOURCE" if cutoff <= 19000 else "POSSIBLE_LOSSY_SOURCE"), levels
        previous = level
    return None, levels
