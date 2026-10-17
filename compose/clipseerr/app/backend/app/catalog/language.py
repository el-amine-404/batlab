"""The language a song is sung in, from its lyrics (langdetect). Only an
answer it is sure of: enough words, and one language well ahead; a title alone
is too short to tell, so a song without lyrics has none."""

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
