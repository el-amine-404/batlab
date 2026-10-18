"""The sites videodrop downloads from: yt-dlp's extractors, as a person
looks for them (GET /api/v1/sites, the page's Sites). One entry a site
(its domain), whatever number of extractors yt-dlp has for it, in a
category, with what can be had from it: videos, playlists, channels, live
streams; whether some of it needs an account; whether yt-dlp says it is
broken now.

yt-dlp knows the sites; it doesn't sort them. A known site is named,
described and sorted here (KNOWN); the others are sorted by words in their
names and domains (_GUESSES), their description made from what yt-dlp has
for them. A site's domain is that of its extractors' own test links (its
URL pattern alone is too loose to read).

Read in a process of its own (python -m videodrop.sites: every extractor
of yt-dlp loaded, which the server needn't keep), once for each yt-dlp
version, and kept in state_dir. A site's icon is asked of DuckDuckGo's icon
service by the server, never by the browser, and kept too (icon())."""

import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from importlib import metadata
from pathlib import Path

import httpx

from . import outbound

CATEGORIES = {
    "video": "Video platforms",
    "social": "Social networks",
    "music": "Music & audio",
    "podcasts": "Podcasts & radio",
    "live": "Live streaming & gaming",
    "tv": "TV & news",
    "film": "Films, series & creators",
    "learning": "Learning & talks",
    "sports": "Sports",
    "adult": "Adult",
    "other": "Other sites",
}

# domain: (name, category, what it is)
KNOWN: dict[str, tuple[str, str, str]] = {
    "youtube.com": ("YouTube", "video", "The largest video site: videos, Shorts, playlists, channels, live streams and music."),
    "vimeo.com": ("Vimeo", "video", "Creators' and companies' videos, often in high quality; showcases and channels."),
    "dailymotion.com": ("Dailymotion", "video", "A French video platform: news, sport, entertainment clips."),
    "rumble.com": ("Rumble", "video", "A video platform for creators, news and live streams."),
    "odysee.com": ("Odysee", "video", "Videos published on the LBRY network."),
    "bitchute.com": ("BitChute", "video", "A video hosting site for independent creators."),
    "bilibili.com": ("Bilibili", "video", "China's video community: anime, games, music, with danmaku comments."),
    "bilibili.tv": ("Bilibili (international)", "video", "Bilibili's international site: anime and creators."),
    "nicovideo.jp": ("Niconico", "video", "Japan's video site with comments flying over the picture."),
    "youku.com": ("Youku", "video", "A large Chinese video and series platform."),
    "iqiyi.com": ("iQIYI", "film", "Chinese series, films and shows."),
    "vk.com": ("VK", "social", "Russia's social network: its videos and clips."),
    "vkvideo.ru": ("VK Video", "video", "VK's video service."),
    "ok.ru": ("OK", "social", "Odnoklassniki, a Russian social network, and its videos."),
    "rutube.ru": ("Rutube", "video", "A Russian video platform: shows, films, channels."),
    "streamable.com": ("Streamable", "video", "Short clips shared by link."),
    "imgur.com": ("Imgur", "social", "Images and short videos shared online."),
    "archive.org": ("Internet Archive", "other", "Films, TV, concerts and old web videos kept by the Archive."),
    "peertube.tv": ("PeerTube", "video", "Federated video sites run by many hosts."),
    "tiktok.com": ("TikTok", "social", "Short vertical videos, users' pages and sounds."),
    "douyin.com": ("Douyin", "social", "TikTok's Chinese sister app."),
    "instagram.com": ("Instagram", "social", "Posts, reels and stories (some need an account)."),
    "facebook.com": ("Facebook", "social", "Videos, reels and live streams posted on Facebook."),
    "x.com": ("X (Twitter)", "social", "Videos and GIFs in posts."),
    "twitter.com": ("X (Twitter)", "social", "Videos and GIFs in posts."),
    "reddit.com": ("Reddit", "social", "Videos posted to subreddits."),
    "pinterest.com": ("Pinterest", "social", "Video pins."),
    "tumblr.com": ("Tumblr", "social", "Videos in Tumblr posts."),
    "bsky.app": ("Bluesky", "social", "Videos in Bluesky posts."),
    "threads.net": ("Threads", "social", "Videos in Threads posts."),
    "weibo.com": ("Weibo", "social", "China's microblog and its videos."),
    "linkedin.com": ("LinkedIn", "social", "Videos posted on LinkedIn, and LinkedIn Learning courses."),
    "9gag.com": ("9GAG", "social", "Funny clips and memes."),
    "snapchat.com": ("Snapchat", "social", "Public Spotlight videos."),
    "likee.video": ("Likee", "social", "Short videos."),
    "triller.co": ("Triller", "social", "Short music videos."),
    "soundcloud.com": ("SoundCloud", "music", "Tracks, sets and artists from independent musicians."),
    "bandcamp.com": ("Bandcamp", "music", "Albums and tracks sold by artists and labels."),
    "mixcloud.com": ("Mixcloud", "music", "DJ mixes, radio shows and live sets."),
    "audiomack.com": ("Audiomack", "music", "Hip-hop, Afrobeats and new music."),
    "audius.co": ("Audius", "music", "A music streaming network run by artists."),
    "jiosaavn.com": ("JioSaavn", "music", "Indian music streaming."),
    "saavn.com": ("JioSaavn", "music", "Indian music streaming."),
    "music.163.com": ("NetEase Cloud Music", "music", "Chinese music streaming, songs and MVs."),
    "y.qq.com": ("QQ Music", "music", "Tencent's music streaming."),
    "music.yandex.ru": ("Yandex Music", "music", "Russian music streaming."),
    "deezer.com": ("Deezer", "music", "Music streaming (previews)."),
    "vevo.com": ("Vevo", "music", "Official music videos."),
    "hearthis.at": ("hearthis.at", "music", "Mixes and tracks from DJs and producers."),
    "bandlab.com": ("BandLab", "music", "Songs made and shared on BandLab."),
    "nts.live": ("NTS", "podcasts", "An independent online radio: shows and mixes."),
    "podcasts.apple.com": ("Apple Podcasts", "podcasts", "Podcast episodes."),
    "spreaker.com": ("Spreaker", "podcasts", "Podcasts and live radio shows."),
    "castbox.fm": ("Castbox", "podcasts", "Podcast episodes."),
    "iheart.com": ("iHeart", "podcasts", "Podcasts and radio."),
    "podbean.com": ("Podbean", "podcasts", "Podcast episodes."),
    "art19.com": ("Art19", "podcasts", "Podcast episodes."),
    "radiofrance.fr": ("Radio France", "podcasts", "France Inter, France Culture and the others: shows and podcasts."),
    "ardaudiothek.de": ("ARD Audiothek", "podcasts", "German public radio's podcasts and audio."),
    "twitch.tv": ("Twitch", "live", "Live streams, past broadcasts and clips."),
    "kick.com": ("Kick", "live", "Live streams, VODs and clips."),
    "trovo.live": ("Trovo", "live", "Game live streams."),
    "picarto.tv": ("Picarto", "live", "Artists drawing live."),
    "dlive.tv": ("DLive", "live", "Live streams."),
    "medal.tv": ("Medal", "live", "Gaming clips."),
    "steampowered.com": ("Steam", "live", "Games' trailers."),
    "gamejolt.com": ("Game Jolt", "live", "Indie games' videos and posts."),
    "afreecatv.com": ("SOOP (AfreecaTV)", "live", "Korean live streams and VODs."),
    "sooplive.co.kr": ("SOOP", "live", "Korean live streams and VODs."),
    "chzzk.naver.com": ("CHZZK", "live", "Naver's live streaming."),
    "huya.com": ("Huya", "live", "Chinese game live streams."),
    "douyu.com": ("Douyu", "live", "Chinese game live streams."),
    "bbc.co.uk": ("BBC", "tv", "BBC iPlayer, News and Sounds (iPlayer from the UK only)."),
    "bbc.com": ("BBC", "tv", "BBC News and programmes."),
    "cnn.com": ("CNN", "tv", "News videos."),
    "nbcnews.com": ("NBC News", "tv", "News videos."),
    "nbc.com": ("NBC", "tv", "Shows' episodes and clips."),
    "cbsnews.com": ("CBS News", "tv", "News videos and live news."),
    "cbs.com": ("CBS", "tv", "Shows' episodes and clips."),
    "abcnews.go.com": ("ABC News", "tv", "News videos."),
    "foxnews.com": ("Fox News", "tv", "News videos."),
    "aljazeera.com": ("Al Jazeera", "tv", "News videos."),
    "bloomberg.com": ("Bloomberg", "tv", "Business news videos."),
    "euronews.com": ("Euronews", "tv", "European news."),
    "arte.tv": ("ARTE", "tv", "The Franco-German channel's programmes and documentaries."),
    "zdf.de": ("ZDF", "tv", "German public TV's library."),
    "ardmediathek.de": ("ARD Mediathek", "tv", "German public TV's library."),
    "france.tv": ("france.tv", "tv", "French public TV's programmes."),
    "francetvinfo.fr": ("franceinfo", "tv", "French public news."),
    "raiplay.it": ("RaiPlay", "tv", "Italian public TV's library."),
    "rtve.es": ("RTVE", "tv", "Spanish public TV and radio."),
    "nhk.or.jp": ("NHK", "tv", "Japan's public broadcaster, NHK World included."),
    "cbc.ca": ("CBC", "tv", "Canada's public broadcaster."),
    "abc.net.au": ("ABC (Australia)", "tv", "Australia's public broadcaster: iview and news."),
    "svtplay.se": ("SVT Play", "tv", "Swedish public TV."),
    "nrk.no": ("NRK", "tv", "Norwegian public TV and radio."),
    "dr.dk": ("DR", "tv", "Danish public TV."),
    "yle.fi": ("Yle Areena", "tv", "Finnish public TV and radio."),
    "itv.com": ("ITVX", "tv", "ITV's programmes (from the UK)."),
    "channel4.com": ("Channel 4", "tv", "Channel 4's programmes (from the UK)."),
    "9now.com.au": ("9Now", "tv", "Nine's programmes (from Australia)."),
    "7plus.com.au": ("7plus", "tv", "Seven's programmes (from Australia)."),
    "tv5monde.com": ("TV5MONDE", "tv", "French-language international TV."),
    "globo.com": ("Globo", "tv", "Brazilian TV."),
    "ted.com": ("TED", "learning", "TED talks, with their subtitles."),
    "udemy.com": ("Udemy", "learning", "Online courses (an account is needed)."),
    "coursera.org": ("Coursera", "learning", "University courses."),
    "khanacademy.org": ("Khan Academy", "learning", "Free lessons, from maths to history."),
    "pluralsight.com": ("Pluralsight", "learning", "Tech courses (an account is needed)."),
    "skillshare.com": ("Skillshare", "learning", "Creative classes (an account is needed)."),
    "ocw.mit.edu": ("MIT OpenCourseWare", "learning", "MIT's course lectures."),
    "nebula.tv": ("Nebula", "film", "Educational creators' videos, without ads (an account is needed)."),
    "crunchyroll.com": ("Crunchyroll", "film", "Anime episodes (an account is needed)."),
    "floatplane.com": ("Floatplane", "film", "Creators' videos for subscribers."),
    "patreon.com": ("Patreon", "film", "Creators' posts and videos for their patrons."),
    "curiositystream.com": ("CuriosityStream", "film", "Documentaries (an account is needed)."),
    "dropout.tv": ("Dropout", "film", "CollegeHumor's comedy shows (an account is needed)."),
    "pluto.tv": ("Pluto TV", "film", "Free films and series on demand."),
    "tubitv.com": ("Tubi", "film", "Free films and series."),
    "plex.tv": ("Plex", "film", "Plex's free films and shows."),
    "rokfin.com": ("Rokfin", "film", "Creators' videos and streams."),
    "espn.com": ("ESPN", "sports", "Sports clips and highlights."),
    "nfl.com": ("NFL", "sports", "American football highlights."),
    "nba.com": ("NBA", "sports", "Basketball highlights."),
    "mlb.com": ("MLB", "sports", "Baseball highlights."),
    "nhl.com": ("NHL", "sports", "Hockey highlights."),
    "olympics.com": ("Olympics", "sports", "Olympic Games videos."),
    "redbull.com": ("Red Bull TV", "sports", "Action sports, music and films."),
    "eurosport.com": ("Eurosport", "sports", "Sports clips."),
    "dazn.com": ("DAZN", "sports", "Sports highlights."),
    "ufc.tv": ("UFC Fight Pass", "sports", "Fights (an account is needed)."),
    "pornhub.com": ("Pornhub", "adult", "Adult videos, playlists and models' channels."),
    "xhamster.com": ("xHamster", "adult", "Adult videos and creators' channels."),
    "xvideos.com": ("XVIDEOS", "adult", "Adult videos."),
    "xnxx.com": ("XNXX", "adult", "Adult videos."),
    "redtube.com": ("RedTube", "adult", "Adult videos."),
    "youporn.com": ("YouPorn", "adult", "Adult videos."),
    "spankbang.com": ("SpankBang", "adult", "Adult videos and playlists."),
    "eporner.com": ("Eporner", "adult", "Adult videos in HD."),
    "redgifs.com": ("RedGIFs", "adult", "Short adult clips."),
    "chaturbate.com": ("Chaturbate", "adult", "Adult live cams."),
    "stripchat.com": ("Stripchat", "adult", "Adult live cams."),
}

_GUESSES = (
    ("adult", re.compile(r"porn|xxx|sex|hentai|nsfw|\bcams?\b|camsoda|cam4|bonga|chaturbate|stripchat|tnaflix|empflix|"
                         r"drtuber|nuvid|beeg|youjizz|fux|slutload|erotic|ero(?:profile|cast)|fap|nude|milf|kink|"
                         r"rule34|tube8|txxx|thisvid|zenporn|noodlemagazine|spankbang|xhamster|xvideos|xnxx", re.I)),
    ("podcasts", re.compile(r"radio|podcast|audiothek|\.fm\b|iheart|spreaker|castbox|podbean|sounds", re.I)),
    ("music", re.compile(r"music|sound|song|audio|bandcamp|mixcloud|audiomack|saavn|lyrics|beat|vevo|concert|"
                         r"\bmp3\b|record", re.I)),
    ("sports", re.compile(r"sport|\bnfl\b|\bnba\b|\bmlb\b|\bnhl\b|fifa|uefa|olymp|espn|dazn|eurosport|formula|\bf1\b|"
                          r"\bufc\b|\bwwe\b|redbull|football|soccer|tennis|golf|cricket|rugby|racing", re.I)),
    ("live", re.compile(r"twitch|\bkick\b|stream|\blive\b|game|gaming|esports|trovo|dlive|picarto|steam", re.I)),
    ("learning", re.compile(r"learn|course|academy|\.edu\b|lecture|\bted\b|udemy|coursera|school|universit|lesson|"
                            r"tutorial|class", re.I)),
    ("tv", re.compile(r"\btv\b|tv\.|television|televis|fernsehen|mediathek|news|nachrichten|broadcast|channel|\bzdf\b|"
                      r"\bard\b|\brai\b|\bnhk\b|\bbbc\b|\bcbs\b|\bnbc\b|\babc\b|\bfox\b|\bcnn\b|\barte\b|\brtl\b|rtve|"
                      r"\brtp\b|\bnos\b|\bsvt\b|\bnrk\b|\byle\b|\borf\b|\bsrf\b|\brts\b|\btvp\b|ertflix|\bitv\b|"
                      r"\bsbs\b|\bcbc\b|\bctv\b|globo|televisa|\bplay\b", re.I)),
    ("film", re.compile(r"film|movie|cinema|series|anime|drama|documentar|trailer|vod\b|ott\b|premium", re.I)),
    ("tv", re.compile(r"episode|programme|program\b|\bshow|broadcaster|public service", re.I)),
    # CamelCase names: NationalGeographicTV, TeleBruxelles; papers' sites are news.
    ("tv", re.compile(r"TV(?![a-z])|Tele[A-Z]|\brtl|Mail\b|Reporter|Times\b|Post\b|Herald|Guardian|Tribune|Gazette|"
                      r"Journal|Daily[A-Z]|Zeitung|Spiegel|Corriere|Repubblica|Figaro|Monde")),
    ("video", re.compile(r"video|tube|clip|vid\b", re.I)),
)
# Second-level names under a country: example.co.uk is one site, not co.uk.
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"}
# What an extractor's name says it reads.
_KINDS = (
    ("playlists", re.compile(r"playlist|album|set\b|collection|series|season|show", re.I)),
    ("channels", re.compile(r"channel|user|artist|profile|creator|model|uploader|tab\b|page\b", re.I)),
    ("live streams", re.compile(r"live|stream", re.I)),
    ("clips", re.compile(r"clip|short|reel|story", re.I)),
    ("podcasts", re.compile(r"podcast|episode", re.I)),
)
ICON_SERVICE = "https://icons.duckduckgo.com/ip3/{domain}.ico"
ICON_MAX = 200_000
# A site without an icon is asked again after this long.
ICON_RETRY = 7 * 86400
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def site_of(host: str) -> str:
    """music.youtube.com → youtube.com; www.bbc.co.uk → bbc.co.uk; a few
    sites that are a part of a bigger one stay whole (KNOWN: music.163.com)."""
    host = host.lower().removeprefix("www.").removeprefix("m.")
    labels = host.split(".")
    for size in range(len(labels), 1, -1):
        if ".".join(labels[-size:]) in KNOWN:
            return ".".join(labels[-size:])
    keep = 3 if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


def _category(domain: str, name: str, text: str, adult: bool) -> str:
    if adult:
        return "adult"
    words = f"{domain} {name} {text}"
    return next((category for category, pattern in _GUESSES if pattern.search(words)), "other")


def _name(domain: str, extractors: list[dict]) -> str:
    """The site's name as yt-dlp says it (its main extractor's description,
    when that is a name), else its domain's."""
    for extractor in sorted(extractors, key=lambda e: len(e["name"])):
        desc = extractor["desc"] or ""
        if desc and len(desc) <= 30 and not re.search(r"[;:(),]", desc):
            return re.sub(r"\s+(?:videos?|extractor|lists?|clips?)$", "", desc, flags=re.I) or desc
    main = sorted(extractors, key=lambda e: len(e["name"]))[0]["name"].split(":")[0]
    return main if re.search(r"[A-Z]", main) else domain


def _describe(kinds: list[str], account: bool, desc: str | None) -> str:
    what = ["videos", *kinds]
    text = (what[0] if len(what) == 1 else f"{', '.join(what[:-1])} and {what[-1]}").capitalize() + "."
    if desc and len(desc) > 30:
        text = f"{desc.rstrip('.')}. {text}"
    return text + (" Some need an account (cookies or a login)." if account else "")


def collect() -> list[dict]:
    """Every site yt-dlp downloads from (run in a process of its own: it loads every extractor)."""
    from yt_dlp.extractor import gen_extractor_classes

    sites: dict[str, list[dict]] = {}
    for ie in gen_extractor_classes():
        name = ie.IE_NAME
        if ie.IE_DESC is False or name == "generic" or "embed" in name.lower() or name.endswith(":search"):
            continue
        try:
            tests = [t.get("url") or "" for t in ie.real_class.get_testcases(include_onlymatching=True)]
        except Exception:  # an extractor's own bug: it isn't listed
            continue
        links = [url for url in tests if url.startswith(("http://", "https://"))]
        if not links:
            continue
        host = urllib.parse.urlsplit(links[0]).hostname or ""
        if not _DOMAIN.match(host):
            continue
        sites.setdefault(site_of(host), []).append({
            "name": name, "desc": ie.IE_DESC if isinstance(ie.IE_DESC, str) else None, "working": bool(ie._WORKING),
            "adult": (getattr(ie, "age_limit", 0) or 0) >= 18, "account": bool(ie._NETRC_MACHINE),
            "examples": [url for url in links if len(url) <= 200][:2]})
    found = []
    for domain, extractors in sites.items():
        known = KNOWN.get(domain)
        names = " ".join(e["name"] for e in extractors)
        kinds = [kind for kind, pattern in _KINDS if pattern.search(names)]
        account = any(e["account"] for e in extractors)
        main = sorted(extractors, key=lambda e: len(e["name"]))[0]
        name = known[0] if known else _name(domain, extractors)
        # yt-dlp marks a site 18+ by its tests; a big site with one such test isn't an adult site.
        adult = not known and all(e["adult"] for e in extractors)
        examples = []
        for extractor in extractors:
            examples += [url for url in extractor["examples"] if url not in examples]
        found.append({
            "id": domain, "name": name, "domain": domain, "url": f"https://{domain}",
            "category": known[1] if known else _category(domain, name, " ".join(f"{e['name']} {e['desc'] or ''}"
                                                                                 for e in extractors), adult),
            "description": known[2] if known else _describe(kinds, account, main["desc"]),
            "kinds": kinds, "account": account, "broken": not any(e["working"] for e in extractors),
            "known": bool(known), "extractors": len(extractors), "examples": examples[:3],
        })
    return sorted(found, key=lambda site: (not site["known"], site["name"].lower()))


class Sites:
    def __init__(self, state_dir: Path, http: httpx.AsyncClient):
        self._folder = state_dir / "sites"
        self._http = http
        self._lock = threading.Lock()
        self._cached: tuple[str, list[dict]] | None = None

    def list(self) -> list[dict]:
        """The sites of the yt-dlp running here; read once a version (a few seconds the first time)."""
        version = metadata.version("yt-dlp")
        if self._cached and self._cached[0] == version:
            return self._cached[1]
        with self._lock:
            if self._cached and self._cached[0] == version:
                return self._cached[1]
            saved = self._folder / f"sites-{version}.json"
            try:
                sites = json.loads(saved.read_text())
            except (OSError, ValueError):
                answer = subprocess.run([sys.executable, "-m", "videodrop.sites"], capture_output=True, text=True,
                                        timeout=120, check=True)
                sites = json.loads(answer.stdout)
                self._folder.mkdir(parents=True, exist_ok=True)
                for old in self._folder.glob("sites-*.json"):
                    old.unlink(missing_ok=True)
                partial = saved.with_suffix(".part")
                partial.write_text(json.dumps(sites))
                partial.replace(saved)
            self._cached = (version, sites)
            return sites

    async def icon(self, domain: str) -> Path | None:
        """The site's icon, asked of DuckDuckGo once and kept; None when it
        has none (asked again a week later). Only a listed site's."""
        if not _DOMAIN.match(domain) or domain not in {site["domain"] for site in self.list()}:
            return None
        icons = self._folder / "icons"
        found, missing = icons / f"{domain}.ico", icons / f"{domain}.none"
        if found.is_file():
            return found
        if missing.is_file() and time.time() - missing.stat().st_mtime < ICON_RETRY:
            return None
        icons.mkdir(parents=True, exist_ok=True)
        try:
            answer = await outbound.call(self._http, "GET", ICON_SERVICE.format(domain=domain))
            body = answer.content
            kind = answer.headers.get("content-type", "")
            # A picture only, never an SVG (a document of its own).
            ok = kind.startswith("image/") and "svg" not in kind and 0 < len(body) <= ICON_MAX
        except outbound.SendFailed:
            ok, body = False, b""
        if not ok:
            missing.touch()
            return None
        partial = icons / f".{domain}.ico"
        partial.write_bytes(body)
        partial.replace(found)
        return found


if __name__ == "__main__":
    json.dump(collect(), sys.stdout, ensure_ascii=False)
