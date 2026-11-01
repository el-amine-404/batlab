"""What a spoken sentence asks the TV to do, in English, French and Arabic.

Pure text to intent, no I/O. Whisper hears short commands imperfectly ("mais en
pause", "turn of the tv"), so a sentence is matched exactly first and then
loosely against the same phrases. Nothing that is not a known command acts:
"play ..." and "search ..." are the only free-form sentences.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

APPS = {
    "111299001912": ("YouTube", ("youtube", "you tube", "yutub", "يوتيوب", "يوتوب", "اليوتيوب", "اليوتوب")),
    "3201907018807": ("Netflix", ("netflix", "net flix", "نتفليكس", "نيتفليكس", "نتفلكس")),
    "AprZAARz4r.Jellyfin": ("Jellyfin", ("jellyfin", "jelly fin", "jellyfen", "جيليفين", "جلي فين", "جيلي فين")),
    "3201606009684": ("Spotify", ("spotify", "سبوتيفاي", "سبوتيفي")),
    "3201910019365": ("Prime Video", ("prime video", "amazon prime", "prime", "برايم", "برايم فيديو")),
    "3201901017640": ("Disney+", ("disney plus", "disney", "ديزني", "ديزني بلس")),
    "3202010022079": ("Internet", ("internet", "browser", "navigateur", "الانترنت", "انترنت", "المتصفح")),
}

_OPEN = (
    "open|play|watch|launch|start|go to|switch to|put on|show me|show|ouvre|ouvrir|ouvrez|lance|lancer|lancez|mets|mettre|"
    "va sur|aller sur|demarre|affiche|افتح|فتح|حل|شغل|شعل|حط|روح|روح ل|سير ل|دخل"
)


def _fold(text: str) -> str:
    """Lower case, no accents, marks or punctuation; one spelling per Arabic letter."""
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = text.translate(str.maketrans("أإآٱىةئؤ٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "اااايهيو" + "0123456789" * 2))
    text = text.replace("ـ", "").replace("'", " ").replace("’", " ")
    text = re.sub(r"[^\w\s+]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


_POLITE = re.compile(
    r"^(?:(?:please|hey|ok|okay|s il te plait|s il vous plait|stp|svp|من فضلك|عافاك|بالله عليك|يا الله) )+"
    r"|(?: (?:please|now|s il te plait|s il vous plait|stp|svp|من فضلك|عافاك|maintenant|دابا|الان))+$"
)


def normalize(text: str) -> str:
    text = _fold(text)
    while True:
        shorter = _POLITE.sub("", text).strip()
        if shorter == text:
            return text
        text = shorter


def _key(key: str, label: str) -> dict:
    return {"type": "key", "key": key, "label": label}


def _media(action: str, label: str) -> dict:
    return {"type": "media", "action": action, "label": label}


_GROUPS: list[tuple[dict, str]] = [
    ({"type": "power", "action": "off", "label": "Turn off the TV"},
     "turn off|turn off the tv|turn the tv off|turn it off|tv off|power off|switch off|switch off the tv|shut down|"
     "shut down the tv|eteins|eteins la tele|eteindre|eteindre la tele|eteins le televiseur|eteint la tele|"
     "coupe la tele|طفي|اطفي|طفي التلفزيون|اطفي التلفزيون|طفي التلفزه|اطفي التلفزه|طفي التلفاز|اطفي التلفاز|"
     "طفي الشاشه|اطفي الشاشه|سد التلفزيون|سد التلفزه"),
    ({"type": "power", "action": "on", "label": "Turn on the TV"},
     "turn on|turn on the tv|turn the tv on|tv on|power on|switch on|switch on the tv|wake up|wake up the tv|allume|"
     "allume la tele|allumer|allumer la tele|allume le televiseur|شعل|شعل التلفزيون|شعل التلفزه|شعل التلفاز|"
     "شغل التلفزيون|شغل التلفزه|شغل التلفاز|شغل الشاشه|ولع التلفزيون|ولع التلفزه"),
    ({"type": "volume", "dir": 1, "label": "Volume up"},
     "volume up|turn up|turn it up|turn up the volume|turn the volume up|louder|increase volume|increase the volume|"
     "raise volume|raise the volume|sound up|monte|monter|monte le son|monte le volume|monter le son|monter le volume|"
     "augmente|augmente le son|augmente le volume|plus fort|un peu plus fort|ارفع الصوت|علي الصوت|زيد الصوت|"
     "طلع الصوت|صوت اعلي|رفع الصوت"),
    ({"type": "volume", "dir": -1, "label": "Volume down"},
     "volume down|turn down|turn it down|turn down the volume|turn the volume down|quieter|decrease volume|"
     "decrease the volume|lower volume|lower the volume|sound down|baisse|baisser|baisse le son|baisse le volume|"
     "baisser le son|baisser le volume|diminue|diminue le son|diminue le volume|moins fort|un peu moins fort|"
     "نقص الصوت|وطي الصوت|اخفض الصوت|نزل الصوت|هبط الصوت|خفض الصوت|صوت اوطي"),
    ({"type": "mute", "on": True, "label": "Mute"},
     "mute|mute the tv|silence|sound off|coupe le son|couper le son|sourdine|mets en sourdine|mettre en sourdine|"
     "كتم|كتم الصوت|صامت|سكت|سكت الصوت|قطع الصوت|طفي الصوت"),
    ({"type": "mute", "on": False, "label": "Unmute"},
     "unmute|sound on|turn the sound on|remets le son|remettre le son|mets le son|enleve la sourdine|"
     "رجع الصوت|الغاء الكتم|فك الكتم|شغل الصوت"),
    (_media("pause", "Pause"),
     "pause|stop|freeze|hold on|pause the video|mets en pause|mettre en pause|met en pause|arrete|arreter|arret|"
     "attends|وقف|توقف|ايقاف|ايقاف مؤقت|حبس|بلاصي|لحظه"),
    (_media("play", "Play"),
     "play|resume|continue|unpause|lecture|reprends|reprendre|reprise|relance|joue|lire|شغل|كمل|استمر|استئناف"),
    (_media("next", "Next"),
     "next|next video|next episode|skip|suivant|suivante|video suivante|episode suivant|التالي|اللي بعدو|اللي من بعد"),
    (_media("prev", "Previous"),
     "previous|previous video|precedent|precedente|video precedente|السابق|اللي قبل|اللي قبلو"),
    (_key("KEY_HOME", "Home"), "home|go home|home screen|main menu|accueil|page d accueil|الرئيسيه|الصفحه الرئيسيه|الديور"),
    (_key("KEY_RETURN", "Back"), "back|go back|return|retour|reviens|en arriere|رجوع|ارجع|رجع|للخلف"),
    (_key("KEY_MENU", "Menu"), "menu|settings|parametres|reglages|القائمه|الاعدادات|المينو"),
    (_key("KEY_SOURCE", "Input"), "input|source|change input|switch input|changer de source|change de source|المصدر|المدخل"),
    (_key("KEY_INFO", "Info"), "info|information|informations|معلومات"),
    (_key("KEY_GUIDE", "Guide"), "guide|tv guide|guide tv|الدليل"),
    (_key("KEY_UP", "Up"), "up|go up|haut|en haut|vers le haut|فوق|الفوق"),
    (_key("KEY_DOWN", "Down"), "down|go down|bas|en bas|vers le bas|تحت|لتحت"),
    (_key("KEY_LEFT", "Left"), "left|go left|gauche|a gauche|يسار|ليسر|اليسار"),
    (_key("KEY_RIGHT", "Right"), "right|go right|droite|a droite|يمين|ليمن|اليمين"),
    (_key("KEY_ENTER", "OK"), "ok|okay|select|enter|confirm|valide|valider|entrer|موافق|اختار|تاكيد"),
    (_key("KEY_CHUP", "Channel up"),
     "channel up|next channel|chaine suivante|chaine plus|القناه التاليه|القناه الجايه"),
    (_key("KEY_CHDOWN", "Channel down"),
     "channel down|previous channel|chaine precedente|chaine moins|القناه السابقه"),
]

PHRASES: dict[str, dict] = {}
for _intent, _words in _GROUPS:
    for _phrase in _words.split("|"):
        PHRASES.setdefault(_fold(_phrase), _intent)
del _intent, _words, _phrase

_APP_NAMES: dict[str, tuple[str, str]] = {
    _fold(alias): (app_id, name)
    for app_id, (name, aliases) in APPS.items()
    for alias in aliases
}
_APP_RE = re.compile(
    rf"^(?:(?:{'|'.join(_fold(w) for w in _OPEN.split('|'))}) )?(?:(?:the|la|le|l|ال|ل) )?(?:app(?:lication)? |تطبيق )?(.+?)"
    rf"(?: (?:app|application|تطبيق))?$"
)

_TENS = {
    "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100, "one hundred": 100, "five": 5,
    "dix": 10, "quinze": 15, "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
    "cent": 100, "cinq": 5,
    "خمسه": 5, "عشره": 10, "عشرين": 20, "تلاتين": 30, "ثلاثين": 30, "اربعين": 40, "خمسين": 50, "ستين": 60, "ميه": 100, "مئه": 100,
}
_NUM = r"(\d{1,3}|" + "|".join(sorted(map(re.escape, _TENS), key=len, reverse=True)) + ")"


def _number(word: str) -> int:
    return int(word) if word.isdigit() else _TENS[word]


_VOLUME_SET = re.compile(
    rf"^(?:(?:set|put|turn|mets|mettre|regle|حط|دير) )?(?:(?:the|le) )?(?:volume|son|الصوت)"
    rf"(?: (?:to|at|a|sur|على|علي|ف|في))? {_NUM}$"
)
_VOLUME_BY = re.compile(
    rf"^(?:(?P<up>volume up|turn up|turn it up|turn the volume up|monte(?: le (?:son|volume))?|"
    rf"augmente(?: le (?:son|volume))?|ارفع الصوت|زيد الصوت|علي الصوت)|"
    rf"(?P<down>volume down|turn down|turn it down|turn the volume down|baisse(?: le (?:son|volume))?|"
    rf"diminue(?: le (?:son|volume))?|نقص الصوت|نزل الصوت|وطي الصوت))(?: (?:by|de|ب))? {_NUM}$"
)
_UNIT = r"(?: (sec|secs|second|seconds|seconde|secondes|ثانيه|ثواني|min|mins|minute|minutes|دقيقه|دقايق|دقائق))?"
_SEEK_FWD = re.compile(
    rf"^(?:(?:skip|jump|go|fast)(?: forward)?|forward|avance|avancer|avance de|قدم|زيد لقدام)(?: (?:by|de))? {_NUM}{_UNIT}$"
)
_SEEK_BACK = re.compile(
    rf"^(?:(?:go|jump|skip) back|back|rewind|recule|reculer|recule de|رجع|رجع للور)(?: (?:by|de))? {_NUM}{_UNIT}$"
)
_SEEK_FWD_PLAIN = re.compile(r"^(?:forward|fast forward|avance|avance rapide|قدم|زيد لقدام)$")
_SEEK_BACK_PLAIN = re.compile(r"^(?:rewind|recule|reculer|رجع للور)$")

_SEARCH = re.compile(r"^(?:search(?: for)?|find|look for|cherche|chercher|recherche|trouve|ابحث عن|ابحث|بحث عن|قلب على|قلب علي|دور على|دور علي) (.+)$")
_PLAY = re.compile(r"^(?:play|watch|put on|joue|lance|mets|regarder|شغل|شغللي|حط|حطلي|شوف) (.+)$")
_ON_YOUTUBE = re.compile(r" (?:on|sur|in|على|علي|ف|في) (?:" + "|".join(map(re.escape, ("youtube", "you tube", "يوتيوب", "يوتوب", "اليوتيوب", "اليوتوب"))) + ")$")
_SEARCH_RAW = re.compile(r"^\s*(?:search(?:\s+for)?|find|look\s+for|cherche[rz]?|recherche|trouve|ابحث\s+عن|ابحث|بحث\s+عن|قلب\s+عل[ىي]|دور\s+عل[ىي])\s+(.+?)[\s.!?،؟]*$", re.I)
_PLAY_RAW = re.compile(r"^\s*(?:play|watch|put\s+on|joue|lance|mets|regarder|شغل|شغللي|حط|حطلي|شوف)\s+(.+?)[\s.!?،؟]*$", re.I)
_ON_YOUTUBE_RAW = re.compile(r"\s+(?:on|sur|in|على|علي|ف|في)\s+(?:you\s*tube|يوتيوب|يوتوب|اليوتيوب|اليوتوب)\s*$", re.I)

# What Whisper writes for silence and background noise.
_HALLUCINATION = re.compile(
    r"^(?:thank you|thanks|thanks for watching|thank you for watching|you|bye|merci|merci d avoir regarde.*|"
    r"sous titres?.*|sous titrage.*|شكرا|شكرا لكم|شكرا لمشاهدتكم|ترجمه.*|اشتركوا.*|موسيقي)$"
)

_ARABIC_SOUNDS = str.maketrans("صضطظذثقحعغ", "سدتززتكهاك")


def _sounds(text: str) -> str:
    return text.translate(_ARABIC_SOUNDS)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _sounds(a), _sounds(b), autojunk=False).ratio()


def _loose_phrase(text: str) -> dict | None:
    """The nearest known phrase, if near enough. Harder for power: a misheard
    sentence must not switch the TV off."""
    if len(text) < 4 or len(text) > 40:
        return None
    best, score = None, 0.0
    for phrase, intent in PHRASES.items():
        if len(phrase) < 4:
            continue
        s = _similar(text, phrase)
        if s > score:
            best, score = intent, s
    if best is None:
        return None
    if best["type"] == "power":
        # "turn the tv" is as near to on as to off: that is no answer.
        other = max(_similar(text, p) for p, i in PHRASES.items()
                    if i["type"] == "power" and i["action"] != best["action"])
        if score < 0.88 or score - other < 0.05:
            return None
    return best if score >= 0.8 else None


def _app(text: str) -> dict | None:
    m = _APP_RE.match(text)
    candidate = m.group(1) if m else text
    candidate = re.sub(r"^ال(?=\w)", "", candidate) if candidate not in _APP_NAMES else candidate
    hit = _APP_NAMES.get(candidate) or _APP_NAMES.get("ال" + candidate)
    if not hit and len(candidate) >= 5:  # "netflics", "jelly fine"
        best = max(_APP_NAMES, key=lambda n: _similar(candidate, n))
        if candidate[0] == best[0] and _similar(candidate, best) >= 0.8:
            hit = _APP_NAMES[best]
    if not hit:
        return None
    return {"type": "app", "id": hit[0], "name": hit[1], "label": f"Open {hit[1]}"}


def parse(raw: str) -> dict | None:
    """The command in a sentence, or None when it is not one."""
    text = normalize(raw)
    if not text or _HALLUCINATION.match(text):
        return None

    if text in PHRASES:
        return dict(PHRASES[text])

    if m := _VOLUME_SET.match(text):
        level = min(100, _number(m.group(1)))
        return {"type": "volume_set", "level": level, "label": f"Volume {level}"}
    if m := _VOLUME_BY.match(text):
        direction = 1 if m.group("up") else -1
        return {"type": "volume", "dir": direction, "by": min(50, _number(m.group(3))),
                "label": "Volume up" if direction > 0 else "Volume down"}
    for pattern, sign in ((_SEEK_FWD, 1), (_SEEK_BACK, -1)):
        if m := pattern.match(text):
            seconds = _number(m.group(1))
            if (m.group(2) or "").startswith(("min", "دق")):
                seconds *= 60
            return {"type": "media", "action": "rel", "value": sign * seconds,
                    "label": f"{'Forward' if sign > 0 else 'Back'} {seconds}s"}
    if _SEEK_FWD_PLAIN.match(text):
        return {"type": "media", "action": "rel", "value": 30, "label": "Forward 30s"}
    if _SEEK_BACK_PLAIN.match(text):
        return {"type": "media", "action": "rel", "value": -10, "label": "Back 10s"}

    # "lance netflix" before "lance <a video>", and a bare app name.
    if app := _app(text):
        return app

    # Known commands misheard, before anything free-form swallows them.
    if loose := _loose_phrase(text):
        return dict(loose)

    if _SEARCH.match(text):
        m = _SEARCH_RAW.match(raw)
        query = _ON_YOUTUBE_RAW.sub("", m.group(1)) if m else _SEARCH.match(text).group(1)
        return {"type": "search", "query": query, "label": f"Search: {query}"}
    if _PLAY.match(text) and len(text.split()) >= 2:
        m = _PLAY_RAW.match(raw)
        query = _ON_YOUTUBE_RAW.sub("", m.group(1)) if m else _PLAY.match(text).group(1)
        return {"type": "play", "query": query, "label": f"Play: {query}"}
    return None
