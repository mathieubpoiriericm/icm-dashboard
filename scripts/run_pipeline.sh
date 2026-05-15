#!/usr/bin/env bash
set -euo pipefail

# Wrapper for the weekly cron-driven pipeline run.
# Install: add to crontab (replace <repo> with the absolute path):
#   0 3 * * 1 /<repo>/scripts/run_pipeline.sh

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs
TS=$(date -u +%Y%m%dT%H%M%SZ)
LOG="logs/cron_${TS}.log"

# main.py combined mode runs PubMed + sync-external in one invocation with a
# single notification. Chained with the R transform in the same container so
# we pay one cold-start, not three.
docker compose run --rm --entrypoint sh pipeline -c \
  "python pipeline/main.py --pubmed --sync-external-data --days-back 7 && \
   Rscript scripts/trigger_update.R" 2>&1 | tee -a "$LOG"

docker compose restart dashboard 2>&1 | tee -a "$LOG"
