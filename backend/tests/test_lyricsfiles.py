from stanzarr import language, lyricsfiles


def test_lrc_with_tags_and_repeated_stamps():
    text = ("[ar:Band]\n[by:AI-generated, corrected by a person]\n[#:uncertain lines: 2, 3]\n[#:checked lines: 1, 2]\n"
            "[00:01.00][00:20.50] chorus\n[00:05.25] verse\n")
    lyrics = lyricsfiles.parse_lrc(text)
    assert lyrics.synced
    assert [(l.t, l.s) for l in lyrics.lines] == [(1.0, "chorus"), (5.25, "verse"), (20.5, "chorus")]
    origin = lyricsfiles.origin(lyrics)
    assert (origin.source, origin.ai_state, origin.unsure, origin.checked) == ("ai", "corrected", 2, 2)


def test_origins():
    assert lyricsfiles.origin(lyricsfiles.parse_lrc("[by:AI-generated, not checked by a person]\n[00:01.00] a")).ai_state == "unchecked"
    assert lyricsfiles.origin(lyricsfiles.parse_lrc("[by:AI-generated, checked by a person]\n[00:01.00] a")).ai_state == "checked"
    assert lyricsfiles.origin(lyricsfiles.parse_lrc("[re:LRCLIB via Stanzarr]\n[00:01.00] a")).source == "lrclib"
    assert lyricsfiles.origin(lyricsfiles.parse_lrc("[00:01.00] a")).source == "unknown"


def test_word_stamps_are_dropped_from_text():
    lyrics = lyricsfiles.parse_lrc("[00:01.00] <00:01.00>Hello <00:01.50>world")
    assert lyrics.lines[0].s == "Hello world"


def test_ttml():
    lyrics = lyricsfiles.parse_ttml('<tt xml:lang="fr"><body><p begin="00:01.500">Bonjour</p><p begin="1:02.0">toi</p></body></tt>')
    assert [(l.t, l.s) for l in lyrics.lines] == [(1.5, "Bonjour"), (62.0, "toi")]
    assert lyrics.tags["la"] == "fr"


def test_lookalike_letters_only_in_latin_words():
    assert lyricsfiles.clean("ri7tеk\nПривет\n") == "ri7tek\nПривет\n"


def test_render_is_sorted_and_skips_empty():
    out = lyricsfiles.render_lrc([lyricsfiles.Line(5, "b"), lyricsfiles.Line(1.5, "a"), lyricsfiles.Line(3, " ")], ["[re:x]"])
    assert out == "[re:x]\n[00:01.500] a\n[00:05.000] b\n"


def test_language_guess():
    assert language.detect("I know you love me and all the things that you do for me tonight") == "en"
    assert language.detect("Je ne sais pas pourquoi tu me fais ça, c'est pas la vie que je veux pour moi") == "fr"
    assert language.detect("3tit 3lihoum demmi gherssou f gelbi, rani 9adr w wach bghit chi haja m3ak") == "ary"
    assert language.detect("حبيبي يا نور العين") == "ar"
    assert language.detect("anything", "es") == "es"
