"""The settings the page changes (Settings, General), kept in the database;
the environment gives their first values (config.py)."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import Config
from .db import Database

KEY = "settings"

# YouTube Music's chart countries (get_charts), with their names; ZZ is the world.
REGIONS = {
    "ZZ": "Global", "AE": "United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "BE": "Belgium", "BO": "Bolivia", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile",
    "CO": "Colombia", "CR": "Costa Rica", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark",
    "DO": "Dominican Republic", "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt", "ES": "Spain", "FI": "Finland",
    "FR": "France", "GB": "United Kingdom", "GT": "Guatemala", "HK": "Hong Kong", "HN": "Honduras",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IS": "Iceland",
    "IT": "Italy", "JP": "Japan", "KE": "Kenya", "KR": "South Korea", "LU": "Luxembourg", "MX": "Mexico",
    "MY": "Malaysia", "NG": "Nigeria", "NI": "Nicaragua", "NL": "Netherlands", "NO": "Norway",
    "NZ": "New Zealand", "PA": "Panama", "PE": "Peru", "PH": "Philippines", "PL": "Poland", "PT": "Portugal",
    "PY": "Paraguay", "RO": "Romania", "RS": "Serbia", "RU": "Russia", "SA": "Saudi Arabia", "SE": "Sweden",
    "SG": "Singapore", "SV": "El Salvador", "TH": "Thailand", "TR": "Turkey", "TW": "Taiwan", "TZ": "Tanzania",
    "UA": "Ukraine", "UG": "Uganda", "US": "United States", "UY": "Uruguay", "VN": "Vietnam", "ZA": "South Africa",
    "ZW": "Zimbabwe",
}
# Apple storefronts with music video charts (two letters, lower case).
STOREFRONTS = {
    "us": "United States", "gb": "United Kingdom", "fr": "France", "de": "Germany", "es": "Spain", "it": "Italy",
    "ca": "Canada", "au": "Australia", "br": "Brazil", "mx": "Mexico", "jp": "Japan", "kr": "South Korea",
    "in": "India", "nl": "Netherlands", "be": "Belgium", "ch": "Switzerland", "se": "Sweden", "no": "Norway",
    "dk": "Denmark", "pt": "Portugal", "ie": "Ireland", "nz": "New Zealand", "ae": "United Arab Emirates",
    "sa": "Saudi Arabia", "eg": "Egypt", "za": "South Africa", "tr": "Turkey",
}
# The Hot category: YouTube searches whose official videos it lists.
HOT_QUERIES = [
    "twerk official music video",
    "sexy official music video",
    "uncensored official music video",
    "perreo official video",
    "dancehall official music video",
    "afrobeats dance official video",
]


class Settings(BaseModel):
    region: str = Field(description="YouTube Music's charts and new videos: a country code, ZZ for the world.")
    storefront: str = Field(description="Apple Music's charts: a storefront code.")
    poll_seconds: int = Field(ge=5, le=600, description="How often services are asked where requests are.")
    attempt_timeout_hours: int = Field(ge=1, le=168, description="How long a service may take before the next "
                                                                 "one gets the request.")
    hide_explicit: bool = Field(description="Leave explicit videos out of the lists.")
    hot_queries: list[str] = Field(description="The Hot category: YouTube searches, each giving its official videos.")
    hot_feeds: list[str] = Field(description="The Hot category: feeds of videos (a clipfeed items URL, or any app "
                                             "answering the same JSON).")


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: str | None = None
    storefront: str | None = None
    poll_seconds: int | None = Field(None, ge=5, le=600)
    attempt_timeout_hours: int | None = Field(None, ge=1, le=168)
    hide_explicit: bool | None = None
    hot_queries: list[str] | None = Field(None, max_length=12)
    hot_feeds: list[str] | None = Field(None, max_length=8)

    @field_validator("hot_feeds")
    @classmethod
    def _hot_feeds(cls, value):
        if value is None:
            return value
        feeds = list(dict.fromkeys(u.strip() for u in value if u.strip()))
        for url in feeds:
            if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url) or len(url) > 500:
                raise ValueError(f"{url!r} isn't a web address (http://… or https://…).")
        return feeds

    @field_validator("hot_queries")
    @classmethod
    def _hot_queries(cls, value):
        if value is None:
            return value
        queries = list(dict.fromkeys(" ".join(q.split())[:80] for q in value if q.strip()))
        if not queries:
            raise ValueError("Give the Hot category at least one search (the feeds come on top).")
        return queries

    @field_validator("region")
    @classmethod
    def _region(cls, value):
        if value is not None and value.upper() not in REGIONS:
            raise ValueError("That isn't a country YouTube Music has charts for.")
        return value.upper() if value else value

    @field_validator("storefront")
    @classmethod
    def _storefront(cls, value):
        if value is not None and value.lower() not in STOREFRONTS:
            raise ValueError("That isn't an Apple Music storefront clipseerr knows.")
        return value.lower() if value else value


class SettingsStore:
    def __init__(self, db: Database, config: Config):
        self._db = db
        self._defaults = {
            "region": config.region if config.region in REGIONS else "US",
            "storefront": config.storefront if config.storefront in STOREFRONTS else "us",
            "poll_seconds": 15,
            "attempt_timeout_hours": 12,
            "hide_explicit": False,
            "hot_queries": list(HOT_QUERIES),
            "hot_feeds": list(config.hot_feeds),
        }
        self._value = self._load()

    def _load(self) -> dict:
        saved = self._db.get(KEY, {}) or {}
        return {**self._defaults, **{k: v for k, v in saved.items() if k in self._defaults}}

    def __call__(self) -> dict:
        return dict(self._value)

    def update(self, patch: SettingsPatch) -> dict:
        changes = patch.model_dump(exclude_none=True)
        saved = self._db.get(KEY, {}) or {}
        saved.update(changes)
        self._db.put(KEY, saved)
        self._value = self._load()
        return self()
