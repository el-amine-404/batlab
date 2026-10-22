"""Which language lyrics are in: a light guess, no model.

An LRC [la:] tag wins. Otherwise the script decides Arabic, and common words
decide between Latin-script languages. Darija (Moroccan Arabic) is often
written in Latin letters with digits for Arabic sounds ("3lik", "7obb",
"9albi"): words like that make it "ary", ISO 639-3 for Moroccan Arabic.
"""

import re
import unicodedata

NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "de": "German", "it": "Italian", "pt": "Portuguese",
    "ar": "Arabic", "ary": "Darija", "nl": "Dutch", "tr": "Turkish", "ru": "Russian", "ja": "Japanese",
    "ko": "Korean", "zh": "Chinese", "": "Unknown",
}

WORDS = {
    "en": "the and you i to a it in my me is that of your on for be no what this don't love know we all just like",
    "fr": "je de la le et les tu que pas des un une est mon ma moi pour dans sur qui c'est j'ai ça on plus avec",
    "es": "de la que el y en no me te mi lo es un por con tu una los se para yo como más pero",
    "de": "ich und die der nicht du das ist es zu ein mich mir sie wir mit auf den dich so",
    "it": "di che non la il e un mi ti è per una con sono come ma lo ho tu me",
    "pt": "que de não eu a o e você me se um é do da meu minha pra com mais",
    "nl": "ik de het een en je niet dat van is in me mijn op",
    "tr": "ve bir ben sen bu da de ne için gibi çok var yok",
}
WORD_SETS = {lang: set(words.split()) for lang, words in WORDS.items()}
ARABIZI = re.compile(r"(?i)\b[a-z]*[23579][a-z]+[a-z0-9]*\b|\b[a-z]+[23579]\b")
DARIJA = set("wach bghit bghat kayn chi hna ana nta nti dyal dyali ghir bzaf mzyan rani raha 3la walo khoya "
             "safi daba lyoum gelbi galbi 9albi kanbghik bnadem m3a 7ta wla hadi hada had".split())


def _script(text: str) -> str:
    counts: dict[str, int] = {}
    for char in text:
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        script = name.split(" ")[0] if name else ""
        if script in ("CJK", "HIRAGANA", "KATAKANA"):
            script = "CJK"
        counts[script] = counts.get(script, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else ""


def detect(text: str, tag: str = "") -> str:
    if tag:
        return tag.strip().lower()[:8]
    script = _script(text)
    if script == "ARABIC":
        return "ar"
    if script == "CYRILLIC":
        return "ru"
    if script == "HANGUL":
        return "ko"
    if script in ("HIRAGANA", "KATAKANA"):
        return "ja"
    if script == "CJK":
        return "zh"
    words = re.findall(r"[\w']+", text.lower())
    if not words:
        return ""
    darija = sum(1 for w in words if w in DARIJA) + len(ARABIZI.findall(text.lower()))
    scores = {lang: sum(1 for w in words if w in s) for lang, s in WORD_SETS.items()}
    best = max(scores, key=lambda k: scores[k])
    if darija >= max(3, len(words) * 0.06) and darija >= scores[best] * 0.5:
        return "ary"
    return best if scores[best] >= max(2, len(words) * 0.04) else ""


def name(code: str) -> str:
    return NAMES.get(code, code)
