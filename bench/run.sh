#!/usr/bin/env bash
# Reproduce the benchmark: bench/run.sh [--build]
# Needs Docker (colima on macOS: containers reach the 127.0.0.1 fixture as host.lima.internal) and Playwright for
# crawlhound. Tools run one at a time, so each public sandbox site sees at most 1 request per second.
# Firecrawl results come from its hosted API (MCP) and are not re-run here.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "--build" ]]; then
  for t in scrapy crawl4ai crawlee; do docker build -q -t "smbench-$t:1" -f "bench/docker/$t.Dockerfile" bench/docker/; done
fi
for tool in crawlhound crawlee crawl4ai scrapy katana; do
  for target in fixture quotes-js quotes-scroll books; do
    echo "== $tool $target"
    python3 bench/run.py "$tool" "$target"
  done
done
python3 bench/score.py
