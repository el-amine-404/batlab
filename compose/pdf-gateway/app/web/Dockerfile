# syntax=docker/dockerfile:1.29
FROM node:26.13.1-alpine3.24@sha256:6bd9ab43c0ec0190ca7b2d193906cb8b7edf6e7f4afa43ddeccd6452710bffb5 AS build
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

FROM caddy:2@sha256:4851e54a46b206517c574821bb5cc67f170d2156041e9fbc4c9193b45e84a75b
# The image's caddy carries a file capability (bind :80), which cannot exec under
# no-new-privileges with all capabilities dropped. We listen on 8080, so run a
# copy without it (cp does not carry file capabilities).
RUN cp /usr/bin/caddy /usr/local/bin/caddy && chmod 0755 /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /batlab/compose/pdf-gateway/app/web/dist /srv
USER 10006:10006
EXPOSE 8080
CMD ["/usr/local/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
