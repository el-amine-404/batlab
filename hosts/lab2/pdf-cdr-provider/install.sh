#!/usr/bin/env bash

# Install the CDR provider as a systemd user unit for the rootless Dangerzone
# account. Run as that account, not root. It is NOT enabled or started here:
# enabling is a deployment decision taken only after the M3 gate passes.
set -Eeuo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Run as the rootless Dangerzone account, not root" >&2
  exit 1
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -d -m 0700 "$HOME/.local/lib/pdf-cdr-provider" "$HOME/.config/pdf-cdr-provider" "$HOME/.config/systemd/user"
install -m 0600 "$here/provider.py" "$HOME/.local/lib/pdf-cdr-provider/provider.py"
install -m 0644 "$here/pdf-cdr-provider.service" "$HOME/.config/systemd/user/pdf-cdr-provider.service"
env_file="$HOME/.config/pdf-cdr-provider/provider.env"
if [[ ! -f $env_file ]]; then
  # The socket directory must be the one bind-mounted into the cdr-worker.
  printf '%s\n' \
    'PDF_CDR_SOCKET=/mnt/docker-volumes/pdf-gateway/cdr-socket/cdr.sock' \
    'PDF_CDR_ALLOWED_UID=10004' \
    'PDF_CDR_TIMEOUT_SECONDS=600' \
    'PDF_CDR_OCR_LANGUAGE=fra' >"$env_file"
  chmod 0600 "$env_file"
fi
# Added after the first install: a searchable text layer (one language).
grep -q '^PDF_CDR_OCR_LANGUAGE=' "$env_file" || echo 'PDF_CDR_OCR_LANGUAGE=fra' >>"$env_file"
systemctl --user daemon-reload
echo "Installed. Not enabled. Review $env_file, then: systemctl --user enable --now pdf-cdr-provider"
echo "A persistent user service also requires: loginctl enable-linger $USER"
