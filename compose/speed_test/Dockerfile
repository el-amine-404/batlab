FROM debian:12-slim
ARG USER_UID=1000
ARG USER_GID=${USER_UID}
ARG USERNAME=appuser

LABEL version="0.0.1"
LABEL description="image to run the speed test CLI from okla: https://www.speedtest.net/apps/cli"

# 1. install dependecies
RUN DEBIAN_FRONTEND=noninteractive apt-get update && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates gnupg && \
  rm -rf /var/lib/apt/lists/*

# Add ookla repo using their script (INSIDE CONTAINER, not on host)
RUN curl -fsSL https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash && \
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends speedtest && \
  rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid $USER_GID $USERNAME \
  && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME

USER $USER_UID:$USER_GID
WORKDIR /home/$USERNAME

HEALTHCHECK --interval=10m --timeout=10s --start-period=30s --retries=1 \
  CMD speedtest -h > /dev/null 2>&1 || exit 1

ENTRYPOINT ["speedtest", "--accept-license"]
