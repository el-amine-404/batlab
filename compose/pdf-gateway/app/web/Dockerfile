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

FROM caddy:2@sha256:4e2425f6009577e7c651010f16679c28f7d12df3e7b1914f941a7a3370746b3c
# The image's caddy carries a file capability (bind :80), which cannot exec under
# no-new-privileges with all capabilities dropped. We listen on 8080, so run a
# copy without it (cp does not carry file capabilities).
RUN cp /usr/bin/caddy /usr/local/bin/caddy && chmod 0755 /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /batlab/compose/pdf-gateway/app/web/dist /srv
USER 10006:10006
EXPOSE 8080
CMD ["/usr/local/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
