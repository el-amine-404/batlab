"""One aligner over one song, in its own process (each model fills a small GPU):

  python -m stanzarr_worker.align whisper|whisperL|mms vocals.wav lines.json out.json [language]

Writes the start time of every line (or null) to out.json.
"""

import json
import re
import sys
import unicodedata

try:
    import av
    _orig_av_open = av.open
    def _safe_av_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return _orig_av_open(*args, **kwargs)
    av.open = _safe_av_open
except Exception:
    pass

LOOKALIKE = str.maketrans("еаорсухЕАОРСХ", "eaopcyxEAOPCX")
# Darija written in Latin letters uses digits for sounds Latin lacks.
ARABIZI = {"2": "", "3": "a", "5": "kh", "7": "h", "8": "gh", "9": "q", "6": "t"}


def load_vocals(path: str):
    import soundfile
    import torch
    import torchaudio
    audio, rate = soundfile.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return torchaudio.functional.resample(torch.from_numpy(audio), rate, 16000)


def romanize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = "".join(ARABIZI.get(c, c) for c in text)
    return [w for w in (re.sub(r"[^a-z']", "", w) for w in text.split()) if w]


def mms(wave, lines: list[str]) -> list[float | None]:
    """Meta's MMS forced aligner, on the CPU: the GPU belongs to Whisper."""
    import torch
    import torchaudio
    bundle = torchaudio.pipelines.MMS_FA
    model, tokenizer, aligner = bundle.get_model(with_star=True).eval(), bundle.get_tokenizer(), bundle.get_aligner()
    chunk, pad = 20 * 16000, 16000
    emissions = []
    with torch.inference_mode():
        for start in range(0, len(wave), chunk):
            lo, hi = max(0, start - pad), min(len(wave), start + chunk + pad)
            em, _ = model(wave[lo:hi].unsqueeze(0))
            per_sample = em.shape[1] / (hi - lo)
            a = round((start - lo) * per_sample)
            b = a + round((min(start + chunk, len(wave)) - start) * per_sample)
            emissions.append(em[:, a:b])
    emission = torch.cat(emissions, dim=1)
    words, owner = ["*"], [None]
    for i, text in enumerate(lines):
        for w in romanize(text) or ["a"]:
            words.append(w)
            owner.append(i)
        words.append("*")
        owner.append(None)
    spans = aligner(emission[0], tokenizer(words))
    seconds_per_frame = len(wave) / emission.shape[1] / 16000
    starts: dict[int, float] = {}
    for span, i in zip(spans, owner):
        if i is not None and i not in starts:
            starts[i] = span[0].start * seconds_per_frame
    return [starts.get(i) for i in range(len(lines))]


def whisper(wave, lines: list[str], size: str, language: str) -> list[float | None]:
    """Whisper (int8 on GPU, fallback to CPU on OOM) through stable-ts: places the known lines."""
    import stable_whisper
    import torch
    result = None
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            model = stable_whisper.load_faster_whisper(size, device="cuda", compute_type="int8_float16")
            result = model.align(wave.numpy(), "\n".join(lines), language=language, original_split=True, verbose=None)
        except Exception as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
                torch.cuda.empty_cache()
                model = stable_whisper.load_faster_whisper(size, device="cpu", compute_type="int8")
                result = model.align(wave.numpy(), "\n".join(lines), language=language, original_split=True, verbose=None)
            else:
                raise
    else:
        model = stable_whisper.load_faster_whisper(size, device="cpu", compute_type="int8")
        result = model.align(wave.numpy(), "\n".join(lines), language=language, original_split=True, verbose=None)

    key = lambda t: re.sub(r"[^\w]+", "", t.translate(LOOKALIKE).lower())
    pool: dict[str, list[float]] = {}
    for seg in result.segments:
        pool.setdefault(key(seg.text), []).append(seg.start)
    return [pool[key(t)].pop(0) if pool.get(key(t)) else None for t in lines]


def transcribe(vocals_path: str, size: str = "large-v3", language: str = "") -> list[dict]:
    """Transcribe audio from scratch with Whisper (GPU int8, fallback to CPU on OOM)."""
    import stable_whisper
    import torch
    lang = None if language in ("", "auto", "none") else language
    result = None
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            model = stable_whisper.load_faster_whisper(size, device="cuda", compute_type="int8_float16")
            result = model.transcribe(vocals_path, language=lang, vad=True)
        except Exception as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
                torch.cuda.empty_cache()
                model = stable_whisper.load_faster_whisper(size, device="cpu", compute_type="int8")
                result = model.transcribe(vocals_path, language=lang, vad=True)
            else:
                raise
    else:
        model = stable_whisper.load_faster_whisper(size, device="cpu", compute_type="int8")
        result = model.transcribe(vocals_path, language=lang, vad=True)

    out = []
    for seg in result.segments:
        text = seg.text.strip()
        if text:
            out.append({"t": round(seg.start, 3), "s": text, "u": False})
    return out


def main() -> None:
    method = sys.argv[1]
    if method == "transcribe":
        vocals, _, out = sys.argv[2:5]
        language = sys.argv[5] if len(sys.argv) > 5 else ""
        result = transcribe(vocals, "large-v3", language)
        json.dump(result, open(out, "w"))
        return
    vocals, lines_path, out = sys.argv[2:5]
    language = sys.argv[5] if len(sys.argv) > 5 else "fr"
    lines = json.load(open(lines_path, encoding="utf-8"))
    wave = load_vocals(vocals)
    if method == "mms":
        starts = mms(wave, lines)
    elif method == "whisperL":
        starts = whisper(wave, lines, "large-v3", language)
    else:
        starts = whisper(wave, lines, "large-v3-turbo", language)
    json.dump(starts, open(out, "w"))


if __name__ == "__main__":
    main()
