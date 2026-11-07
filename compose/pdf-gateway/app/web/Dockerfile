# syntax=docker/dockerfile:1.28
FROM node:26.13.0-alpine3.24@sha256:8996bb5821d62a8fd9bb27c1cb07462f3fdce6e47d640c6a881ead0b064ce216 AS build
# Where the page is in the repo, beside lib/batlab-ui (the context "ui", the
# frame shared by batlab's apps), so the relative paths hold in both.
WORKDIR /batlab/compose/pdf-gateway/app/web
COPY --from=ui . /batlab/lib/batlab-ui
COPY package.json package-lock.json ./
# The lockfile pins every package with an integrity hash.
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM caddy:2@sha256:ce773ec7a742ce15ee6bf2bb5f65882b9e2a4487b10733e6f1484f86e6b5d8bc
# The image's caddy carries a file capability (bind :80), which cannot exec under
# no-new-privileges with all capabilities dropped. We listen on 8080, so run a
# copy without it (cp does not carry file capabilities).
RUN cp /usr/bin/caddy /usr/local/bin/caddy && chmod 0755 /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /batlab/compose/pdf-gateway/app/web/dist /srv
USER 10006:10006
EXPOSE 8080
CMD ["/usr/local/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
