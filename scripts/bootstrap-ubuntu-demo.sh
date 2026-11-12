#!/usr/bin/env bash
# Run once on a fresh Ubuntu Intel desktop. Does not install drivers or alter audio.
set -euo pipefail
if [[ ${1:-} == --check ]]; then
  for cmd in git python3 docker dpkg-deb patch ffplay pactl systemctl; do command -v "$cmd" >/dev/null || { echo "Missing: $cmd"; exit 1; }; done
  python3 -c 'import yaml,sys; assert sys.version_info >= (3,11)'
  docker info >/dev/null
  docker compose version
  echo 'Host commands available. Build preflight checks locked images and GPU access.'
  exit
fi
if [[ $EUID != 0 || -z ${1:-} ]]; then echo "Usage: sudo $0 DESKTOP_USER (or $0 --check)"; exit 2; fi
user=$1
id "$user" >/dev/null
. /etc/os-release
[[ $ID == ubuntu ]] || { echo 'Ubuntu required'; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'x86-64 required'; exit 1; }
# Refuse to reconfigure an existing Docker installation; use --check and the docs.
if command -v docker >/dev/null; then echo 'Docker already installed; use --check. No host changes made.'; exit 1; fi
apt-get update
apt-get install -y ca-certificates curl git python3 python3-yaml patch ffmpeg pulseaudio-utils
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf 'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: %s\nComponents: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.sources
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
# Enable OCI image identities required by the baseline locks. Fresh installation only.
if [[ -e /etc/docker/daemon.json ]]; then echo 'Existing daemon.json requires review; not overwritten.'; exit 1; fi
install -d /etc/docker
printf '{"features":{"containerd-snapshotter":true}}\n' > /etc/docker/daemon.json
systemctl restart docker
usermod -aG docker,video "$user"
if getent group render >/dev/null; then usermod -aG render "$user"; fi
echo 'Bootstrap complete. Log out and back in as the desktop user before deployment.'
