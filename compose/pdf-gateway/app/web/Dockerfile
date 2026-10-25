# syntax=docker/dockerfile:1.7
FROM node:24.21.0-alpine3.24@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS build
WORKDIR /src
COPY package.json package-lock.json ./
# The lockfile pins every package with an integrity hash.
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM caddy:2@sha256:f2a1290d0463aad60660d4ec134943f183ee2a5f6c3eb7bf32dd984f2f020772
# The image's caddy carries a file capability (bind :80), which cannot exec under
# no-new-privileges with all capabilities dropped. We listen on 8080, so run a
# copy without it (cp does not carry file capabilities).
RUN cp /usr/bin/caddy /usr/local/bin/caddy && chmod 0755 /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv
USER 10006:10006
EXPOSE 8080
CMD ["/usr/local/bin/caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
