# NodeCast pinned in compose/versions.env (IMAGE_/TAG_NODECAST), where Renovate
# keeps it current; compose passes it in.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# The guide sync saves its channels in playlist_items under the same key as the
# live channels, sourceId:id. Dispatcharr's guide ids are channel numbers, its
# stream ids are channel ids: guide channel 13 overwrote the name of stream 13,
# so "2M Monde +1" (channel number 13, stream 669) played stream 13. Give guide
# channels their own key; the guide reads them by item_id, which stays the same.
# The grep fails the build if a new version changes the line.
RUN f=/app/server/services/syncService.js && \
    sed -i 's/const id = `${sourceId}:${ch.id}`;/const id = `${sourceId}:epg:${ch.id}`;/' "$f" && \
    grep -q 'const id = `${sourceId}:epg:${ch.id}`;' "$f"
