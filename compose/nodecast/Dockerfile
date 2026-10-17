# NodeCast pinned in compose/versions.env (IMAGE_/TAG_NODECAST), where Renovate
# keeps it current; compose passes it in.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# The remux (MPEG-TS to MP4, the player's choice for H.264 with AAC, the usual
# Dispatcharr stream) copies ADTS AAC into MP4 without aac_adtstoasc: ffmpeg
# stops at the first audio packet ("Malformed AAC bitstream") and the player
# waits forever. The filter refuses other audio codecs, so a raw-TS channel with
# MP3, Opus or Vorbis audio would now fail here instead; AAC is the norm.
RUN f=/app/server/routes/remux.js && \
    sed -i "s/'-bsf:v', 'dump_extra',/'-bsf:v', 'dump_extra', '-bsf:a', 'aac_adtstoasc',/" "$f" && \
    grep -q "'-bsf:a', 'aac_adtstoasc'," "$f"
