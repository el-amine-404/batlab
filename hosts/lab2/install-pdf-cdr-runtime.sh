#!/usr/bin/env bash

# Install the upstream-supported Dangerzone CDR runtime on Debian 13. This is a
# host prerequisite, not a Compose service: nesting it in Docker would weaken
# the Podman + gVisor boundary that Dangerzone provides.
set -Eeuo pipefail

readonly EXPECTED_FINGERPRINT="DE28AB241FA48260FAC9B8BAA7C9B38522604281"
readonly KEYRING="/etc/apt/keyrings/fpf-apt-tools-archive-keyring.gpg"
readonly SOURCE="/etc/apt/sources.list.d/fpf-apt-tools.list"
readonly CDR_CONFIG_DIR="/etc/batlab/pdf-cdr"
readonly CONTAINERS_CONF="$CDR_CONFIG_DIR/containers.conf"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo hosts/lab2/install-pdf-cdr-runtime.sh" >&2
  exit 1
fi

. /etc/os-release
if [[ ${ID:-} != debian || ${VERSION_CODENAME:-} != trixie ]]; then
  echo "Refusing unsupported host: expected Debian 13 (trixie), got ${PRETTY_NAME:-unknown}" >&2
  exit 1
fi

runtime_user="${SUDO_USER:-}"
if [[ -z $runtime_user || $runtime_user == root ]] || ! id "$runtime_user" >/dev/null 2>&1; then
  echo "Run through sudo as the non-root account that will execute conversions." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates gpg
install -d -m 0755 /etc/apt/keyrings

key_home="$(mktemp -d)"
key_tmp="$(mktemp)"
cleanup() { rm -rf -- "$key_home" "$key_tmp"; }
trap cleanup EXIT

gpg --batch --homedir "$key_home" --keyserver hkps://keys.openpgp.org \
  --recv-keys "$EXPECTED_FINGERPRINT"
fingerprint="$(gpg --batch --homedir "$key_home" --with-colons --fingerprint \
  "$EXPECTED_FINGERPRINT" | awk -F: '$1 == "fpr" {print $10; exit}')"
if [[ $fingerprint != "$EXPECTED_FINGERPRINT" ]]; then
  echo "Dangerzone repository signing-key fingerprint mismatch" >&2
  exit 1
fi
gpg --batch --homedir "$key_home" --export "$EXPECTED_FINGERPRINT" >"$key_tmp"
install -o root -g root -m 0644 "$key_tmp" "$KEYRING"

source_line="deb [signed-by=$KEYRING] https://packages.freedom.press/apt-tools-prod $VERSION_CODENAME main"
if [[ ! -f $SOURCE ]] || [[ $(<"$SOURCE") != "$source_line" ]]; then
  printf '%s\n' "$source_line" >"$SOURCE"
  chmod 0644 "$SOURCE"
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y dangerzone-full runsc

# Keep the gVisor selection scoped to the PDF gateway. Dangerzone disables
# cgroups for rootless conversions, so Podman must also know that runsc supports
# that mode; otherwise it rejects the conversion before the sandbox starts.
install -d -o root -g root -m 0755 "$CDR_CONFIG_DIR"
config_tmp="$(mktemp)"
cleanup_config() { rm -f -- "$config_tmp"; }
trap 'cleanup; cleanup_config' EXIT
cat >"$config_tmp" <<'EOF'
[engine]
runtime = "runsc"
runtime_supports_nocgroups = ["runsc"]

[engine.runtimes]
runsc = ["/usr/bin/runsc"]
EOF
install -o root -g root -m 0644 "$config_tmp" "$CONTAINERS_CONF"

# Rootless execution is part of the boundary. Never initialize or convert as root.
run_as_runtime_user() {
  runuser -u "$runtime_user" -- env CONTAINERS_CONF="$CONTAINERS_CONF" "$@"
}

run_as_runtime_user dangerzone-cli --version
# The Debian package contains the verifier and signing key, but not necessarily
# the sandbox image. `upgrade` retrieves the signed transparency-log metadata
# and imports the current upstream image; verification must happen afterwards.
run_as_runtime_user dangerzone-image upgrade
run_as_runtime_user dangerzone-image verify-local

configured_runtime="$(run_as_runtime_user podman info --format '{{.Host.OCIRuntime.Name}}')"
if [[ $configured_runtime != runsc ]]; then
  echo "Dangerzone Podman configuration selected '$configured_runtime', expected 'runsc'" >&2
  exit 1
fi

echo "Dangerzone CDR runtime and signed image installed with gVisor; run the tracked isolation verification next."

