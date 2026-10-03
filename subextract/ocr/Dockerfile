# Tesseract OCR for Blu-ray (PGS) subtitles; see subextract/README.md.
FROM debian:trixie-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr python3 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# The "best" LSTM models: slower than Debian's packaged ones, more accurate on
# the few hundred lines of an episode. Pinned to the 4.1.0 release.
ENV TESSDATA_PREFIX=/usr/share/tessdata-best
RUN mkdir -p "$TESSDATA_PREFIX" \
 && for lang in eng fra ara ita spa por deu osd; do \
      curl -fsSL -o "$TESSDATA_PREFIX/$lang.traineddata" \
        "https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/$lang.traineddata" || exit 1; \
    done \
 && apt-get purge -y curl && apt-get autoremove -y

COPY pgsocr.py /app/pgsocr.py
RUN useradd --uid 1000 --no-create-home ocr
USER ocr
ENTRYPOINT ["python3", "/app/pgsocr.py"]
