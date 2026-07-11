name: ${__SERVICE_UPPER___PROJECT_NAME}
services:
  __SERVICE__:
    image: ${IMAGE___SERVICE_UPPER__}:${TAG___SERVICE_UPPER__}
    container_name: __SERVICE__
    restart: unless-stopped

    volumes:

    ports:

    environment:

    networks:
      - home_server

    healthcheck:

networks:
  home_server:
    external: true
    name: home_server