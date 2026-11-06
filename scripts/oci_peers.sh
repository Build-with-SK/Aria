#!/usr/bin/env bash
set -euo pipefail
export PUPPETEER_SKIP_DOWNLOAD=true
export ANONYMIZED_TELEMETRY=false
export BROWSER_USE_CLOUD_SYNC=false
export DO_NOT_TRACK=1
install -d -m 700 "$HOME/.local/toolchains" "$HOME/deploy/node"
cd "$HOME/deploy/node"
curl -fsSLO https://nodejs.org/dist/v24.22.2/node-v24.22.2-linux-arm64.tar.xz
curl -fsSLO https://nodejs.org/dist/v24.22.2/SHASUMS256.txt
sha256sum --check --ignore-missing SHASUMS256.txt
tar -xJf node-v24.22.2-linux-arm64.tar.xz -C "$HOME/.local/toolchains"
export PATH="$HOME/.local/toolchains/node-v24.22.2-linux-arm64/bin:$PATH"
node --version
cd "$HOME/apps/gods-eye-view"
npm ci --no-audit --no-fund > "$HOME/deploy/globe-install.log" 2>&1
cd "$HOME/apps/browser-use"
curl -LsSf https://astral.sh/uv/install.sh -o "$HOME/deploy/uv-install.sh"
sh "$HOME/deploy/uv-install.sh" > "$HOME/deploy/uv-install.log" 2>&1
if [[ -f uv.lock ]]; then
    "$HOME/.local/bin/uv" sync --frozen --no-dev > "$HOME/deploy/browser-use-install.log" 2>&1
else
    "$HOME/.local/bin/uv" sync --no-dev > "$HOME/deploy/browser-use-install.log" 2>&1
fi
