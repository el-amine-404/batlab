FROM debian:13-slim AS builder
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
  curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash && \
  apt-get install -y --no-install-recommends speedtest && \
  rm -rf /var/lib/apt/lists/*

FROM debian:13-slim
ARG USER_UID=1000
ARG USER_GID=${USER_UID}
ARG USERNAME=appuser

LABEL version="0.0.1"
LABEL org.opencontainers.image.description="image to run the speed test CLI from okla: https://www.speedtest.net/apps/cli"

# Create non-root user
RUN groupadd --gid $USER_GID $USERNAME && \
  useradd --uid $USER_UID --gid $USER_GID -m $USERNAME

# Copy ONLY the binary and necessary SSL certs from the builder stage
COPY --from=builder /usr/bin/speedtest /usr/bin/speedtest
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

USER $USER_UID:$USER_GID
WORKDIR /home/$USERNAME

ENTRYPOINT ["speedtest", "--accept-license"]