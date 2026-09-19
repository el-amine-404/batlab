FROM ubuntu:22.04 AS build
ARG UNTRUNC_REV=9d86ec9ef2ffed1bf8131abe80742c0574db52b6
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git g++ make libavformat-dev libavcodec-dev libavutil-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init && git remote add origin https://github.com/anthwlock/untrunc.git \
    && git fetch --depth 1 origin "$UNTRUNC_REV" && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$UNTRUNC_REV" && make -j2 && strip untrunc

FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg python3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/untrunc /usr/local/bin/untrunc
COPY untrunc/scripts/ /opt/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work
USER 1000:1000
ENTRYPOINT ["python3", "/opt/recover.py"]
CMD ["inventory"]
