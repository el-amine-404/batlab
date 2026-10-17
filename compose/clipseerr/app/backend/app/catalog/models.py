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
    official: bool = Field(True, description="The artist's own video (YouTube's OMV), not a fan upload.")
    preview_url: str | None = Field(None, description="A 30 s preview (Apple), playable without YouTube.")


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
    audio: AudioMatch | None = Field(None, description="Its official audio, when one matches.")
    links: dict[str, str] = Field(default_factory=dict, description="youtube, youtube_music, apple.")
    description: str | None = None


class Shelf(BaseModel):
    id: str
    title: str
    subtitle: str | None = None
    source: Source
    items: list[Video]
    total: int = Field(description="Items the shelf has in all; items may hold only the first.")


class Category(BaseModel):
    id: str = Field(description='"yt:<playlist id>" (a YouTube chart) or "am:<Apple genre id>".')
    title: str
    group: str
    source: Source
    image: str | None = None


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
