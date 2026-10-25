# syntax=docker/dockerfile:1.7
FROM debian:13.1-slim@sha256:a347fd7510ee31a84387619a492ad6c8eb0af2f2682b916ff3e643eb076f925a AS tools
ARG DIDIER_STEVENS_COMMIT=7d625d0b05c4638b981a2a2cc9a8481d7b73e56d
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && git clone --filter=blob:none --no-checkout https://github.com/DidierStevens/DidierStevensSuite.git /suite \
 && git -C /suite checkout "$DIDIER_STEVENS_COMMIT" -- pdfid.py pdf-parser.py \
 && rm -rf /suite/.git /var/lib/apt/lists/*

FROM debian:13.1-slim@sha256:a347fd7510ee31a84387619a492ad6c8eb0af2f2682b916ff3e643eb076f925a
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pika python3-psycopg2 qpdf yara poppler-utils \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10002 analyzer \
 && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin analyzer
COPY --from=tools /suite/pdfid.py /opt/pdf-tools/pdfid.py
COPY --from=tools /suite/pdf-parser.py /opt/pdf-tools/pdf-parser.py
COPY --chown=10002:10002 scan.py /opt/pdf-gateway/scan.py
COPY --chown=10002:10002 worker.py /opt/pdf-gateway/worker.py
# poppler: renders only sanitized CDR output (blank-page checks); pdftotext reads
# originals' native text under the analysis rlimits (untrusted, organisation only).
COPY --chown=10002:10002 final_validate.py /opt/pdf-gateway/final_validate.py
COPY --chown=10002:10002 extract_text.py /opt/pdf-gateway/extract_text.py
COPY --chown=10002:10002 validation_worker.py /opt/pdf-gateway/validation_worker.py
COPY rules /opt/pdf-gateway/rules
USER 10002:10002
WORKDIR /work
ENTRYPOINT ["python3", "/opt/pdf-gateway/worker.py"]
