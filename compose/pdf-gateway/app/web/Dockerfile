# syntax=docker/dockerfile:1.28
FROM node:24.22.2-alpine3.24@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS build
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

FROM caddy:2@sha256:36c24ad91a7ff7bc4c87fe22bfa8df3c4b18da0773d73fc12ee2c52b98c0bb7e
# The image's caddy carries a file capability (bind :80), which cannot exec under
# no-new-privileges with all capabilities dropped. We listen on 8080, so run a
# copy without it (cp does not carry file capabilities).
RUN cp /usr/bin/caddy /usr/local/bin/caddy && chmod 0755 /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /batlab/compose/pdf-gateway/app/web/dist /srv
USER 10006:10006
EXPOSE 8080
CMD ["/usr/local/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
