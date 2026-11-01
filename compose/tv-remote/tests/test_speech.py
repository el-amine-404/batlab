"""Choosing among what Whisper heard in each language."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
import speech  # noqa: E402


def hearing(**by_language):
    def hear(pcm, lang):
        text, logprob = by_language[lang]
        return {"text": text, "language": lang, "no_speech": 0.01, "logprob": logprob}
    return hear


class Understand(unittest.TestCase):
    def run_it(self, language="auto", **by_language):
        with mock.patch.object(speech, "hear", hearing(**by_language)):
            return speech.understand(b"", language, ["en", "fr", "ar"])

    def test_a_copied_prompt_loses_to_the_real_language(self):
        said = self.run_it(en=("Irfan Sout.", -1.3), fr=("Éteins la télé.", -1.0), ar=("ارفع الصوت.", -0.4))
        self.assertEqual(said["intent"]["type"], "volume")

    def test_recent_language_needs_no_second_opinion(self):
        said = self.run_it(en=("Pause.", -0.3), fr=("x", 0), ar=("x", 0))
        self.assertEqual(said["language"], "en")

    def test_the_most_confident_sentence_decides_even_if_no_command(self):
        said = self.run_it(en=("Hello there", -0.2), fr=("Éteins la télé.", -1.2), ar=("كلام", -1.1))
        self.assertIsNone(said["intent"])

    def test_silence_in_every_language(self):
        self.assertIsNone(self.run_it(en=("", -9), fr=("", -9), ar=("", -9))["intent"])


if __name__ == "__main__":
    unittest.main()
