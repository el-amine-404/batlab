"""The language a song is sung in, from its lyrics (langdetect). Only an
answer it is sure of: enough words, and one language well ahead. A title is
too short for that, but its writing can be enough: a script only one
language is written in (by_script)."""

import re

from langdetect import DetectorFactory, LangDetectException, detect_langs

# The same text, the same answer, every time.
DetectorFactory.seed = 0

MIN_WORDS = 20
MIN_PROBABILITY = 0.8
_WORD = re.compile(r"\w+")

NAMES = {
    "af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali", "ca": "Catalan", "cs": "Czech",
    "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "fa": "Persian", "fi": "Finnish", "fr": "French", "gu": "Gujarati", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "kn": "Kannada", "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian", "ml": "Malayalam",
    "mr": "Marathi", "ne": "Nepali", "nl": "Dutch", "no": "Norwegian", "pa": "Punjabi", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "sq": "Albanian", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tl": "Tagalog", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
    "zh-cn": "Chinese", "zh-tw": "Chinese",
}


def detect(text: str | None) -> str | None:
    """The language's code ("en", "el", "zh-cn"), or None when unsure."""
    if not text or len(_WORD.findall(text)) < MIN_WORDS:
        return None
    try:
        best = detect_langs(text)[0]
    except LangDetectException:
        return None
    return best.lang if best.prob >= MIN_PROBABILITY and best.lang in NAMES else None


def name(code: str | None) -> str | None:
    return NAMES.get(code or "")


# Scripts one language is written in: the title says it then, even with no lyrics.
# (Arabic, Cyrillic, Devanagari and Chinese characters stand for several: left out.)
SCRIPTS = (
    ("he", re.compile(r"[\u0590-\u05ff]")),
    ("el", re.compile(r"[\u0370-\u03ff]")),
    ("ko", re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")),
    ("ja", re.compile(r"[\u3040-\u30ff]")),
    ("th", re.compile(r"[\u0e00-\u0e7f]")),
    ("bn", re.compile(r"[\u0980-\u09ff]")),
    ("ta", re.compile(r"[\u0b80-\u0bff]")),
    ("te", re.compile(r"[\u0c00-\u0c7f]")),
    ("kn", re.compile(r"[\u0c80-\u0cff]")),
    ("ml", re.compile(r"[\u0d00-\u0d7f]")),
    ("gu", re.compile(r"[\u0a80-\u0aff]")),
    ("pa", re.compile(r"[\u0a00-\u0a7f]")),
)


def by_script(text: str | None) -> str | None:
    """The language a title's writing belongs to alone (Hebrew, Greek, Korean…), or None."""
    letters = [c for c in text or "" if c.isalpha()]
    for code, script in SCRIPTS:
        if letters and sum(1 for c in letters if script.match(c)) >= len(letters) / 2:
            return code
    return None
