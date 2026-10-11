# spotDL's release pins the yt-dlp it shipped with, and YouTube answers an old
# yt-dlp with 403 Forbidden within weeks (v4.5.2 carries 2026.07.04, which
# failed on 2026-10-11 while 2026.8.19 worked). This swaps in the yt-dlp pinned
# in compose/versions.env (VERSION_YTDLP, bumped by Renovate) and changes
# nothing else. [default] brings yt-dlp-ejs, which runs YouTube's challenge
# scripts in the Deno that the spotDL image already ships.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG YTDLP_VERSION
USER root
RUN uv pip install --python /app/.venv/bin/python --no-cache "yt-dlp[default]==${YTDLP_VERSION}"
USER spotdl
