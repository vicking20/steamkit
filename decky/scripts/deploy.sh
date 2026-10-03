#!/usr/bin/env bash
# Usage: scripts/deploy.sh user@host
set -euo pipefail
HOST="${1:?usage: deploy.sh user@host}"
cd "$(dirname "$0")/.."
scripts/package.sh
rsync -az --delete out/SteamKit/ "$HOST":/tmp/SteamKit/
ssh -t "$HOST" 'sudo rm -rf ~/homebrew/plugins/SteamKit && sudo mv /tmp/SteamKit ~/homebrew/plugins/SteamKit && sudo systemctl restart plugin_loader'
