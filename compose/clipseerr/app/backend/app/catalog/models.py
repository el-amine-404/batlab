"""What the catalog answers: videos, artists, shelves (a named list of
videos) and categories. Every source (youtube.py, apple.py) answers these, so
the page never knows where a video came from."""

from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["youtube", "apple"]


class ArtistRef(BaseModel):
    name: str
    id: str | None = Field(None, description="YouTube Music channel id, when known: opens /artists/{id}.")


class Video(BaseModel):
    id: str = Field(description='"yt:<YouTube video id>" or "am:<Apple Music id>".')
    source: Source
    youtube_id: str | None = Field(None, description="Known at once for YouTube; found when opened for Apple.")
    title: str = Field(description="The song's name, without (Official Video) and the like.")
    original_title: str | None = Field(None, description="The upload's own title, when it differs.")
    artist: str = Field(description="The credit, as one line.")
    artists: list[ArtistRef]
    thumbnail: str | None = None
    duration: int | None = Field(None, description="Seconds.")
    views: str | None = Field(None, description='As YouTube writes it: "1.2M".')
    released: str | None = Field(None, description="YYYY-MM-DD.")
    genre: str | None = None
    explicit: bool = False
    official: bool = Field(True, description="The artist's own video (a label's upload, or one on the artist's "
                                             "own channel), not a fan upload.")
    preview_url: str | None = Field(None, description="A 30 s preview (Apple), playable without YouTube.")
    library: list[Literal["audio", "video"]] = Field(
        default_factory=list, description="What the libraries (Settings, Libraries) have of it: its song, its video.")


class AudioMatch(BaseModel):
    """The song itself on YouTube Music (its official audio), for listening and
    for a download that should be the album version, not the video's cut."""

    youtube_id: str
    title: str
    artist: str
    album: str | None = None
    year: str | None = None
    duration: int | None = None
    thumbnail: str | None = None


class VideoDetail(Video):
    embeddable: bool = Field(True, description="YouTube lets other pages play it.")
    uploader: str | None = Field(None, description="The YouTube channel that uploaded it, as it names itself.")
    audio: AudioMatch | None = Field(None, description="Its official audio, when one matches.")
    links: dict[str, str] = Field(default_factory=dict, description="youtube, youtube_music, apple.")
    description: str | None = None


class Version(Video):
    """Another take of a video's song: its clean or explicit cut, its official
    video, a remix, a live one."""

    kind: Literal["video", "song"] = Field(description="A music video, or the song (its official audio).")
    label: str = Field(description='What sets it apart: "Clean", "Explicit", "Remix", "Live", "Original"…')


class Versions(BaseModel):
    videos: list[Version]
    songs: list[Version]


class Shelf(BaseModel):
    id: str
    title: str
    subtitle: str | None = None
    source: Source
    items: list[Video]
    total: int = Field(description="Items the shelf has in all; items may hold only the first.")
    updated_at: float | None = Field(None, description="When its oldest part was fetched from its source (Unix time).")


class Category(BaseModel):
    id: str = Field(description='"yt:<playlist id>" (a YouTube chart) or "am:<Apple genre id>".')
    title: str
    group: str
    source: Source
    image: str | None = Field(None, description="Its cover, when its source has one (YouTube's charts).")
    images: list[str] = Field(default_factory=list, description="Pictures of its top videos, for one without a cover.")


class CategoryGroup(BaseModel):
    title: str
    source: Source
    categories: list[Category]


class ArtistSummary(BaseModel):
    id: str
    name: str
    thumbnail: str | None = None
    subscribers: str | None = None


class ArtistPage(ArtistSummary):
    description: str | None = None
    views: str | None = None
    videos: list[Video]
    related: list[ArtistSummary] = Field(default_factory=list)


class SearchResults(BaseModel):
    query: str
    artists: list[ArtistSummary]
    videos: list[Video]
    total: int = Field(0, description="Videos found in all; videos holds one page of them.")
    offset: int = 0


class Credit(BaseModel):
    role: str = Field(description='"Performed by", "Written by", "Produced by"…, as YouTube Music says.')
    names: list[str]


class Credits(BaseModel):
    """Who made the song, from YouTube Music's credits page."""

    credits: list[Credit]
    provider: str | None = Field(None, description="Who gave YouTube Music the credits (the label).")


class LyricLine(BaseModel):
    start: float = Field(description="Seconds into the song.")
    text: str


class Lyrics(BaseModel):
    """The song's words: timed lines when a source has them, else plain text."""

    synced: list[LyricLine] = Field(default_factory=list)
    plain: str | None = None
    instrumental: bool = False
    source: str | None = Field(None, description='"LRCLIB" or "YouTube Music (LyricFind)".')
    language: str | None = Field(None, description='What they are sung in, when sure: "en", "el", "zh-cn".')
    language_name: str | None = Field(None, description='The same in English: "Greek".')


class HotArtist(BaseModel):
    name: str
    id: str | None = Field(None, description="Their YouTube Music page, once found.")
    thumbnail: str | None = None
    videos: int = Field(description="Their videos Hot has shown.")
    first_seen: float
    last_seen: float
