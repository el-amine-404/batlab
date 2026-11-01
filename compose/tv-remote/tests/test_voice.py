"""The sentences voice.py must and must not take for commands."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
from profiles import profile_for  # noqa: E402
import voice  # noqa: E402

INPUTS = profile_for()["inputs"]


def parse(text):
    return voice.parse(text, INPUTS)


CASES = {
    # apps
    "Open YouTube.": ("app", "id", "111299001912"),
    "Ouvre Netflix.": ("app", "id", "3201907018807"),
    "lance jellyfin": ("app", "id", "AprZAARz4r.Jellyfin"),
    "افتح يوتيوب،": ("app", "id", "111299001912"),
    "شغل نتفليكس": ("app", "id", "3201907018807"),
    "حل اليوتيوب": ("app", "id", "111299001912"),
    "Netflics": ("app", "id", "3201907018807"),
    "Disney plus": ("app", "id", "3201901017640"),
    # power
    "Turn off the TV.": ("power", "action", "off"),
    "Éteins la télé.": ("power", "action", "off"),
    "اطفو التلفزيون.": ("power", "action", "off"),
    "أطفئ التلفزيون": ("power", "action", "off"),
    "Allume la télé": ("power", "action", "on"),
    "شعل التلفزة": ("power", "action", "on"),
    # volume
    "Volume up.": ("volume", "dir", 1),
    "Monte le son.": ("volume", "dir", 1),
    "ارفع الصوت.": ("volume", "dir", 1),
    "baisse le volume": ("volume", "dir", -1),
    "نقص الصوت": ("volume", "dir", -1),
    "Set the volume to 20.": ("volume_set", "level", 20),
    "volume vingt": ("volume_set", "level", 20),
    "الصوت ٢٠": ("volume_set", "level", 20),
    "volume up by 10": ("volume", "by", 10),
    "mute": ("mute", "on", True),
    "Coupe le son": ("mute", "on", True),
    "تتم الصوت.": ("mute", "on", True),
    "unmute": ("mute", "on", False),
    # playback: "arrête" pauses, it never switches the TV off
    "Pause.": ("media", "action", "pause"),
    "Mais en pause.": ("media", "action", "pause"),
    "arrête": ("media", "action", "pause"),
    "وقف": ("media", "action", "pause"),
    "شغل": ("media", "action", "play"),
    "reprends": ("media", "action", "play"),
    "next": ("media", "action", "next"),
    "forward 30 seconds": ("media", "value", 30),
    "go back 2 minutes": ("media", "value", -120),
    "recule de 10 secondes": ("media", "value", -10),
    # navigation
    "Retour.": ("key", "key", "KEY_RETURN"),
    "go home please": ("key", "key", "KEY_HOME"),
    "à gauche": ("key", "key", "KEY_LEFT"),
    "يمين": ("key", "key", "KEY_RIGHT"),
    # inputs
    "Switch to HDMI 1.": ("input", "id", "hdmi1"),
    "switch to HDMI one": ("input", "id", "hdmi1"),
    "HDMI-2": ("input", "id", "hdmi2"),
    "hdmi2": ("input", "id", "hdmi2"),
    "Passe en HDMI 1": ("input", "id", "hdmi1"),
    "switch to the DVR": ("input", "id", "hdmi2"),
    "go to the TV box": ("input", "id", "hdmi1"),
    "switch to blu-ray": ("input", "id", "hdmi1"),
    "switch to live TV": ("input", "id", "tv"),
    "HDMI 3": ("input", "id", "hdmi3"),
    "next input": ("input_next", "type", "input_next"),
    "change input": ("key", "key", "KEY_SOURCE"),
    # volume by an amount
    "Turn the volume up by 10.": ("volume", "by", 10),
    "turn the volume down by 5": ("volume", "by", 5),
    "lower the volume by 3": ("volume", "by", 3),
    "baisse le volume de 5": ("volume", "by", 5),
    # places
    "Go to homepage.": ("key", "key", "KEY_HOME"),
    "go to the home page": ("key", "key", "KEY_HOME"),
    "Go to settings": ("key", "key", "KEY_MENU"),
    "ouvre les paramètres": ("key", "key", "KEY_MENU"),
    "Go to Wi-Fi": ("settings_hint", "label", "Settings"),
    "wifi settings": ("settings_hint", "label", "Settings"),
    # repeats, channels
    "down 3 times": ("key", "times", 3),
    "channel 5": ("channel", "number", "5"),
    "restart": ("media", "action", "restart"),
    # free form keeps the words as spoken
    "Play Interstellar trailer.": ("play", "query", "Interstellar trailer"),
    "Mets Interstellar sur YouTube": ("play", "query", "Interstellar"),
    "cherche Les Misérables": ("search", "query", "Les Misérables"),
    "ابحث عن أغاني": ("search", "query", "أغاني"),
}

NOT_COMMANDS = [
    "", "...", "Thank you.", "Merci.", "Sous-titres réalisés par la communauté d'Amara.org", "شكرا",
    "It's a lovely day.", "Multi-four.", "After he'll deal.", "the weather is nice in Paris today",
    "I think we should turn around", "turn tv", "Menopause.", "Turn the TV.", "turn of the tv",
]


class Parse(unittest.TestCase):
    def test_commands(self):
        for said, (kind, field, want) in CASES.items():
            with self.subTest(said=said):
                got = parse(said)
                self.assertIsNotNone(got)
                self.assertEqual((got["type"], got.get(field)), (kind, want))

    def test_not_commands(self):
        for said in NOT_COMMANDS:
            with self.subTest(said=said):
                self.assertIsNone(parse(said))


if __name__ == "__main__":
    unittest.main()
