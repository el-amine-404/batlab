"""One aligner or transcriber over one song, in its own process (each model fills a small GPU):

  python -m stanzarr_worker.align whisper|whisperL|mms vocals.wav lines.json out.json [language]
  python -m stanzarr_worker.align transcribe vocals.wav meta.json out.json

Writes the aligned timings or transcription output to out.json.
"""

import json
import os
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

VOCAL_ADLIBS = {
    "ooh", "ooh-ooh", "ooh ooh", "yeah", "yea", "whoa", "whoa-oh", "ah", "ahh",
    "uh", "uh-huh", "mmm", "baby", "hey", "la la", "nanana", "alright", "whoo",
    "ray", "hey ray", "come home", "harenia", "narenia", "ouais", "ouh", "woah",
    "uh-oh", "oh-oh", "ha-ha", "haha", "shh"
}


def load_vocals(path: str):
    import soundfile
    import torch
    import torchaudio
    audio, rate = soundfile.read(path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return torchaudio.functional.resample(torch.from_numpy(audio), rate, 16000)


def romanize(text: str, is_arabic: bool = False) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    if is_arabic:
        text = "".join(ARABIZI.get(c, c) for c in text)
    return [w for w in (re.sub(r"[^a-z']", "", w) for w in text.split()) if w]


def mms(wave, lines: list[str], language: str = "en") -> list[float | None]:
    """Meta's MMS forced aligner: runs on CUDA if memory allows, otherwise CPU."""
    import torch
    import torchaudio
    bundle = torchaudio.pipelines.MMS_FA
    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        model = bundle.get_model(with_star=True).eval().to(device)
    except Exception:
        device = "cpu"
        model = bundle.get_model(with_star=True).eval().to("cpu")
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()

    is_ar = language.lower() in ("ar", "ary", "dar", "dz")
    chunk, pad = 20 * 16000, 16000
    emissions = []
    with torch.inference_mode():
        for start in range(0, len(wave), chunk):
            lo, hi = max(0, start - pad), min(len(wave), start + chunk + pad)
            segment = wave[lo:hi].unsqueeze(0).to(device)
            em, _ = model(segment)
            per_sample = em.shape[1] / (hi - lo)
            a = round((start - lo) * per_sample)
            b = a + round((min(start + chunk, len(wave)) - start) * per_sample)
            emissions.append(em[:, a:b].cpu())
    emission = torch.cat(emissions, dim=1)
    words, owner = ["*"], [None]
    for i, text in enumerate(lines):
        clean_words = romanize(text, is_arabic=is_ar)
        for w in clean_words or ["a"]:
            words.append(w)
            owner.append(i)
        words.append("*")
        owner.append(None)
    try:
        spans = aligner(emission[0], tokenizer(words))
        seconds_per_frame = len(wave) / emission.shape[1] / 16000
        starts: dict[int, float] = {}
        for span, i in zip(spans, owner):
            if i is not None and i not in starts and span:
                starts[i] = span[0].start * seconds_per_frame
        return [starts.get(i) for i in range(len(lines))]
    except Exception as e:
        sys.stderr.write(f"MMS align warning: {e}\n")
        return [None] * len(lines)


def whisper(wave, lines: list[str], size: str, language: str) -> list[float | None]:
    """Whisper through stable-ts: places the known lines with high precision."""
    import stable_whisper
    import torch
    lang = None if language in ("", "auto", "none") else language
    result = None
    if torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            model = stable_whisper.load_faster_whisper(size, device="cuda", compute_type="int8_float16")
            result = model.align(wave.numpy(), "\n".join(lines), language=lang, original_split=True, verbose=None)
        except Exception as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
                torch.cuda.empty_cache()
                fallback_size = "large-v3-turbo" if size != "large-v3-turbo" else "medium"
                try:
                    model = stable_whisper.load_faster_whisper(fallback_size, device="cuda", compute_type="int8_float16")
                    result = model.align(wave.numpy(), "\n".join(lines), language=lang, original_split=True, verbose=None)
                except Exception:
                    torch.cuda.empty_cache()
                    model = stable_whisper.load_faster_whisper("medium", device="cpu", compute_type="int8")
                    result = model.align(wave.numpy(), "\n".join(lines), language=lang, original_split=True, verbose=None)
            else:
                raise
    else:
        model = stable_whisper.load_faster_whisper(size, device="cpu", compute_type="int8")
        result = model.align(wave.numpy(), "\n".join(lines), language=lang, original_split=True, verbose=None)

    key = lambda t: re.sub(r"[^\w]+", "", t.translate(LOOKALIKE).lower())
    pool: dict[str, list[float]] = {}
    for seg in result.segments:
        pool.setdefault(key(seg.text), []).append(seg.start)
    return [pool[key(t)].pop(0) if pool.get(key(t)) else None for t in lines]


def _detect_speakers(wave, lines: list[dict], artist_metadata: str = "") -> list[dict]:
    """Detect if multiple singers are present in the song (e.g. duets or features)."""
    import torch
    import torchaudio
    if len(lines) < 8 or not torch.cuda.is_available():
        return lines
    multi_artist = bool(re.search(r"\b(feat\.?|ft\.?|with|&|x)\b", artist_metadata, re.I))
    if not multi_artist:
        return lines
    names = [p.strip() for p in re.split(r"\b(?:feat\.?|ft\.?|with|&)\b", artist_metadata, flags=re.I) if p.strip()]
    if len(names) < 2:
        return lines
    sample_rate = 16000
    features = []
    indices = []
    mfcc_transform = torchaudio.transforms.MFCC(sample_rate=sample_rate, n_mfcc=13,
                                                melkwargs={"n_fft": 400, "hop_length": 160, "n_mels": 23, "center": False})
    for i, l in enumerate(lines):
        lo = max(0, int(l["t"] * sample_rate))
        hi = min(len(wave), int(l.get("end", l["t"] + 2.0) * sample_rate))
        if hi - lo < int(0.5 * sample_rate):
            continue
        seg = wave[lo:hi].unsqueeze(0)
        rms = float(torch.sqrt(torch.mean(seg ** 2)))
        if rms < 0.005:
            continue
        try:
            mf = mfcc_transform(seg).mean(dim=-1).squeeze(0)
            features.append(mf)
            indices.append(i)
        except Exception:
            pass
    if len(features) < 8:
        return lines
    try:
        feats = torch.stack(features)
        mean, std = feats.mean(dim=0), feats.std(dim=0) + 1e-6
        norm = (feats - mean) / std
        c1 = norm[0]
        c2 = norm[torch.argmax(torch.norm(norm - c1, dim=-1))]
        for _ in range(10):
            d1 = torch.norm(norm - c1, dim=-1)
            d2 = torch.norm(norm - c2, dim=-1)
            labels = (d2 < d1).long()
            if torch.sum(labels == 0) > 0:
                c1 = norm[labels == 0].mean(dim=0)
            if torch.sum(labels == 1) > 0:
                c2 = norm[labels == 1].mean(dim=0)
        dist = float(torch.norm(c1 - c2))
        if dist > 1.8:
            main_cluster = 0 if (labels == 0).sum() >= (labels == 1).sum() else 1
            s1_name, s2_name = names[0], names[1]
            last_speaker = None
            for idx, label in zip(indices, labels.tolist()):
                spk = s1_name if label == main_cluster else s2_name
                if spk != last_speaker:
                    lines[idx]["s"] = f"[{spk}] {lines[idx]['s']}"
                    last_speaker = spk
    except Exception:
        pass
    return lines


def transcribe(vocals_path: str, meta: dict, device: str = "cuda") -> dict:
    """Transcribes singing audio with musical prompting, word timestamps,

    smart lyric segmentation, secondary vocal parentheses, and stanza structuring.
    """
    import stable_whisper
    import torch

    artist = meta.get("artist", "")
    title = meta.get("title", "")
    lang_pref = meta.get("language", "")
    lines_ref = meta.get("lines_ref", [])

    model_name = "large-v3-turbo"
    compute_type = "int8_float16" if device == "cuda" else "int8"

    prompt_ref = (" Lyrics excerpt: " + " / ".join(lines_ref[:6])) if lines_ref else ""
    prompt = (
        f"Lyrics for the song '{title}' by {artist}.{prompt_ref} "
        "Song lyrics with stanzas, verse, chorus, backing vocals and ad-libs in parentheses (like this), echoing vocals, and repeated refrains."
    )

    lang_arg = lang_pref if lang_pref and lang_pref not in ("", "auto", "none") else None

    # Load model with fallback
    model = None
    if device == "cuda" and torch.cuda.is_available():
        try:
            torch.cuda.empty_cache()
            model = stable_whisper.load_faster_whisper(model_name, device="cuda", compute_type=compute_type)
        except Exception as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
                torch.cuda.empty_cache()
                try:
                    model = stable_whisper.load_faster_whisper("medium", device="cuda", compute_type=compute_type)
                except Exception:
                    model = stable_whisper.load_faster_whisper("medium", device="cpu", compute_type="int8")
            else:
                model = stable_whisper.load_faster_whisper(model_name, device="cpu", compute_type="int8")
    else:
        model = stable_whisper.load_faster_whisper(model_name, device="cpu", compute_type="int8")

    result = model.transcribe(
        vocals_path,
        language=lang_arg,
        vad=False,  # Gentle processing on isolated vocals preserves soft vocals & ad-libs
        condition_on_previous_text=False,  # Stops repetitive hallucination loops
        initial_prompt=prompt,
        word_timestamps=True,
        compression_ratio_threshold=2.2,
        no_speech_threshold=0.6,
    )

    detected_lang = getattr(result, "language", lang_pref or "en")
    if detected_lang in ("english", "en"):
        detected_lang = "en"
    elif detected_lang in ("french", "fr"):
        detected_lang = "fr"

    # Smart lyric segmentation: natural singing pauses and phrase length
    split_res = result.split_by_gap(max_gap=0.8).split_by_punctuation([',', '.', '?', '!', '—', '-']).split_by_length(max_words=8, max_chars=45)

    raw_lines = []
    for seg in split_res.segments:
        txt = seg.text.strip()
        if txt:
            raw_lines.append({"t": round(seg.start, 3), "end": round(seg.end, 3), "s": txt})

    # Merge consecutive tiny fragments without breath pause (<0.35s gap)
    merged = []
    idx = 0
    while idx < len(raw_lines):
        cur = raw_lines[idx]
        while idx + 1 < len(raw_lines):
            nxt = raw_lines[idx + 1]
            gap = nxt["t"] - cur["end"]
            combined_len = len(cur["s"]) + len(nxt["s"]) + 1
            words_cur = len(cur["s"].split())
            words_nxt = len(nxt["s"].split())
            if gap < 0.35 and combined_len <= 45 and (words_cur + words_nxt) <= 8 and not cur["s"].endswith(('.', '?', '!')):
                cur["end"] = nxt["end"]
                cur["s"] = f"{cur['s']} {nxt['s']}"
                idx += 1
            else:
                break
        merged.append(cur)
        idx += 1

    # Secondary / backing vocal detection & stanza segmentation
    processed = []
    for i, item in enumerate(merged):
        s = item["s"].strip()
        is_secondary = False
        lower = s.lower().strip(" ,.?!-()")
        if lower in VOCAL_ADLIBS or (len(lower.split()) <= 2 and any(v in lower for v in ("ooh", "yeah", "whoa", "hey", "ah", "mmm"))):
            is_secondary = True

        # Overlapping vocal with previous line (backing vocals / ad-lib responses)
        if i > 0 and item["t"] < merged[i - 1]["end"] - 0.2:
            is_secondary = True

        if is_secondary and not (s.startswith("(") and s.endswith(")")):
            s = f"({s})"

        stanza_start = (i == 0) or (item["t"] - merged[i - 1]["end"] >= 2.5)
        processed.append({
            "t": item["t"],
            "end": item["end"],
            "s": s,
            "u": False,
            "stanza_start": stanza_start,
        })

    # Speaker diarization for multi-artist tracks
    try:
        wave = load_vocals(vocals_path)
        processed = _detect_speakers(wave, processed, artist)
    except Exception:
        pass

    return {
        "lines": [{"t": p["t"], "s": p["s"], "u": p["u"], "stanza_start": p.get("stanza_start", False)} for p in processed],
        "detected_language": detected_lang
    }


def main() -> None:
    method = sys.argv[1]
    if method == "transcribe":
        vocals, meta_or_dummy, out = sys.argv[2:5]
        meta = {}
        if os.path.exists(meta_or_dummy):
            try:
                meta = json.load(open(meta_or_dummy, encoding="utf-8"))
            except Exception:
                pass
        device = "cuda" if len(sys.argv) > 5 and sys.argv[5] == "cuda" else ("cuda" if _has_cuda() else "cpu")
        res = transcribe(vocals, meta, device=device)
        json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        return

    vocals, lines_path, out = sys.argv[2:5]
    language = sys.argv[5] if len(sys.argv) > 5 else "en"
    lines = json.load(open(lines_path, encoding="utf-8"))
    wave = load_vocals(vocals)
    if method == "mms":
        starts = mms(wave, lines, language=language)
    elif method == "whisperL":
        starts = whisper(wave, lines, "large-v3", language)
    else:
        starts = whisper(wave, lines, "large-v3-turbo", language)
    json.dump(starts, open(out, "w"))


def _has_cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    main()
