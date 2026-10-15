"""What musicguard can be told (environment variables) and its fixed limits."""

import os

AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".alac", ".opus", ".ogg", ".oga",
                    ".webm", ".mka", ".wav", ".aif", ".aiff"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
LYRIC_EXTENSIONS = {".lrc", ".txt"}
# Files yt-dlp and spotDL leave next to a download; removed with it.
SIDECAR_EXTENSIONS = IMAGE_EXTENSIONS | LYRIC_EXTENSIONS | {".json", ".description"}
PARTIAL_SUFFIXES = (".part", ".ytdl", ".tmp", ".temp", ".frag", ".spotdl")
DANGEROUS_EXTENSIONS = {".exe", ".scr", ".bat", ".cmd", ".com", ".pif", ".lnk", ".msi", ".js",
                        ".jse", ".vbs", ".vbe", ".wsf", ".ps1", ".psm1", ".jar", ".apk", ".dll",
                        ".sh", ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".img", ".dmg",
                        ".html", ".htm", ".svg", ".hta", ".url", ".desktop", ".pdf", ".docm",
                        ".xlsm"}
# Cover files the library may keep beside the tracks.
COVER_NAMES = {"cover", "folder", "front", "album"}

MIN_SECONDS = 5
MAX_DECODE_WARNINGS = 20
LYRICS_RETRY_SECONDS = 30 * 86400
# A song MusicBrainz does not know yet is looked up again after a week.
UPGRADE_RETRY_SECONDS = 7 * 86400
# A single whose song is on no album yet is looked at again after a month.
SINGLE_RETRY_SECONDS = 30 * 86400
ORPHAN_SECONDS = 3600
# "Ready to play": songs listed per album, and albums with a message of their
# own per scan (the others are named in one more).
READY_SONGS = 15
READY_ALBUMS = 5
# What needs a look, by event, in the order the message lists them.
FINDINGS = {"quarantined": "Quarantined", "suspect": "Suspect audio", "flagged": "Flagged",
            "held": "Held in the inbox", "error": "Errors"}
FINDINGS_LISTED = 15
STATE_VERSION = 1
# Events after which Navidrome has something new to index.
LIBRARY_CHANGES = {"filed", "cleaned", "verified", "credited", "quarantined", "reported", "lyrics",
                   "removed", "upgraded", "deduplicated", "handed-over"}


def env(name, default=None):
    value = os.environ.get(name, default)
    return value if value not in ("", None) else default


class Config:
    def __init__(self):
        self.inbox = env("MUSICGUARD_INBOX", "/inbox")
        self.extras = env("MUSICGUARD_EXTRAS", "/data/media/music-extras")
        roots = env("MUSICGUARD_ROOTS", "/data/media/music:/data/media/music-aurral")
        self.roots = [r for r in roots.split(":") if r] + [self.extras]
        # Libraries whose manager owns the tags: checked, never rewritten.
        verify = env("MUSICGUARD_VERIFY_ONLY", "/data/media/music")
        self.verify_only = {r for r in verify.split(":") if r}
        # Shazam (SongRec) identification, and MusicBrainz credits for the
        # libraries whose files carry Lidarr's MusicBrainz IDs.
        self.identify = env("MUSICGUARD_IDENTIFY", "1") == "1"
        fix = env("MUSICGUARD_CREDITS", "/data/media/music")
        self.credit_roots = {r for r in fix.split(":") if r}
        self.quarantine = env("MUSICGUARD_QUARANTINE", "/quarantine")
        # Quarantine is a recycle bin: a day's folder goes after this many days (0: never).
        self.quarantine_days = int(env("MUSICGUARD_QUARANTINE_DAYS", "30"))
        self.state_dir = env("MUSICGUARD_STATE", "/state")
        self.clamd_socket = env("CLAMD_SOCKET", "/run/clamav/clamd.ctl")
        self.clamd_required = env("CLAMD_REQUIRED", "1") == "1"
        self.clamd_max = int(env("CLAMD_MAX_BYTES", str(25 * 1024 * 1024)))
        self.lyrics = env("MUSICGUARD_LYRICS", "1") == "1"
        # Messages go through Apprise (batlab_common.notify): its /notify/<key>
        # address, and the tags naming the two channels. Findings that need a
        # look go to the first; "ready to play", once Navidrome has the new
        # music (not when it lands: Lidarr's own import message is off), to
        # the second.
        self.notify_url = env("MUSICGUARD_NOTIFY_URL")
        self.notify_issues = env("MUSICGUARD_NOTIFY_ISSUES", "download-issues")
        self.notify_ready = env("MUSICGUARD_NOTIFY_READY", "downloads")
        self.inbox_interval = int(env("MUSICGUARD_INBOX_INTERVAL", "60"))
        # Short, because Navidrome only shows new music after a library pass.
        self.roots_interval = int(env("MUSICGUARD_ROOTS_INTERVAL", "120"))
        self.settle = int(env("MUSICGUARD_SETTLE_SECONDS", "120"))
        self.dry_run = env("MUSICGUARD_DRY_RUN", "0") == "1"
        # Songs from links are handed to Lidarr for a proper copy (upgrade.py).
        self.lidarr_url = env("MUSICGUARD_LIDARR_URL", "http://lidarr:8686")
        self.lidarr_key = env("LIDARR_API_KEY")
        self.lidarr_root = env("MUSICGUARD_LIDARR_ROOT", "/data/media/music")
        self.upgrade = env("MUSICGUARD_UPGRADE", "1") == "1" and bool(self.lidarr_key)
        # The same song filed twice: the best copy stays (dedup.py).
        self.dedup = env("MUSICGUARD_DEDUP", "1") == "1"
        # Navidrome scans only when asked (navidrome.py).
        self.navidrome_url = env("NAVIDROME_URL", "http://navidrome:4533")
        self.navidrome_user = env("NAVIDROME_USER")
        self.navidrome_password = env("NAVIDROME_PASSWORD")
