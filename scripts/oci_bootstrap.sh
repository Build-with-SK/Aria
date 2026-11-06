#!/usr/bin/env bash
# Prepare the approved Ubuntu OCI host. Does not start application services.
set -euo pipefail

if [ "$(uname -m)" != aarch64 ]; then
  echo 'Expected an ARM host; refusing bootstrap.' >&2
  exit 1
fi
. /etc/os-release
if [ "$ID" != ubuntu ] || [ "$VERSION_ID" != 24.04 ]; then
  echo 'Expected Ubuntu 24.04; refusing bootstrap.' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
sudo -n apt-get update -qq
sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-dev build-essential pkg-config libssl-dev libffi-dev \
  ca-certificates curl git rsync sqlite3 xz-utils

install -d -m 700 "$HOME/apps" "$HOME/deploy" "$HOME/.config/aria-sentinel"
sudo -n timedatectl set-timezone Europe/London
python3 --version
df -h /
