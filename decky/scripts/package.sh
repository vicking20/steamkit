#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root: the build needs ../steamkit
if ! docker info >/dev/null 2>&1; then
  [ "$(uname)" = Darwin ] && open -a Docker || { echo "Docker is not running"; exit 1; }
  echo "Waiting for Docker…"
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
fi
docker run --rm \
  -v "$PWD":/src \
  -v steamkit-node-modules:/src/decky/node_modules \
  -w /src/decky \
  node:22-slim \
  sh -c "npm install --no-audit --no-fund --loglevel=error && npm run build && chown -R $(id -u):$(id -g) dist py_modules"

cd decky
rm -rf out && mkdir -p out/SteamKit
cp -r dist main.py plugin.json package.json py_modules out/SteamKit/
find out -name __pycache__ -prune -exec rm -rf {} +
(cd out && python3 -m zipfile -c SteamKit.zip SteamKit)
echo "Built: $(pwd)/out/SteamKit.zip"
