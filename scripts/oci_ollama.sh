#!/usr/bin/env bash
# Vendor installer, private loopback only, bounded concurrency on the free host.
set -euo pipefail
install -d -m 700 "$HOME/deploy"
curl -fsSL https://ollama.com/install.sh -o "$HOME/deploy/ollama-install.sh"
sh "$HOME/deploy/ollama-install.sh" > "$HOME/deploy/ollama-install.log" 2>&1
sudo -n install -d -m 755 /etc/systemd/system/ollama.service.d
sudo -n tee /etc/systemd/system/ollama.service.d/aria-sentinel.conf >/dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_CONTEXT_LENGTH=8192"
EOF
sudo -n systemctl daemon-reload
sudo -n systemctl restart ollama
sudo -n systemctl enable ollama >/dev/null
ollama --version
