#!/usr/bin/env bash
# Run a full tuning experiment: extract → validate → analyze → calibrate → track.
#
# Usage:
#   ./scripts/tuning/run_experiment.sh [threshold] [notes] [pdf_path]
#
# Examples:
#   ./scripts/tuning/run_experiment.sh                          # defaults
#   ./scripts/tuning/run_experiment.sh 0.5 "lower threshold test"
#   ./scripts/tuning/run_experiment.sh 0.7 "v2 prompt" pipeline/test_data/37069360.pdf

set -euo pipefail

THRESHOLD="${1:-0.65}"
NOTES="${2:-}"
PDF_PATH="${3:-pipeline/test_data/37069360.pdf}"

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

# Capture all console output so it can be embedded in the JSON report later.
LOG_FILE=$(mktemp "${TMPDIR:-/tmp}/experiment_log.XXXXXX")
REPORT=""

# Embed the captured console log into the JSON report, then clean up.
embed_log() {
  if [ -n "$REPORT" ] && [ -f "$REPORT" ] && [ -f "$LOG_FILE" ]; then
    python3 -c '
import json, sys
report_path, log_path = sys.argv[1], sys.argv[2]
with open(report_path, encoding="utf-8") as f:
    report = json.load(f)
import re
ansi_re = re.compile(r"\x1b\[[0-9;]*m")
with open(log_path, encoding="utf-8") as f:
    report["experiment_log"] = [ansi_re.sub("", line) for line in f.read().splitlines()]
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
    f.write("\n")
' "$REPORT" "$LOG_FILE"
    echo "Console log embedded in: $REPORT"
  fi
  rm -f "$LOG_FILE"
}
trap embed_log EXIT

# Helper: echo to both terminal and log file.
log_echo() {
  echo "$@" | tee -a "$LOG_FILE"
}

# Helper: run a command, sending its stdout+stderr to both terminal and log file.
run_logged() {
  "$@" 2>&1 | tee -a "$LOG_FILE"
}

# Force rich/colorama to emit ANSI colors even though stdout is piped through tee.
export FORCE_COLOR=1

log_echo "=== Tuning Experiment ==="
log_echo "  Threshold:      $THRESHOLD"
log_echo "  PDF:            $PDF_PATH"
log_echo "  Prompt version: ${PIPELINE_PROMPT_VERSION:-v3}"
log_echo "  Notes:          ${NOTES:-<none>}"
log_echo ""

# Step 1: Extract
log_echo "--- Step 1: Running pipeline extraction ---"
PIPELINE_CONFIDENCE_THRESHOLD="$THRESHOLD" \
  run_logged python pipeline/main.py --local-pdfs "$PDF_PATH"

# Find the latest report
REPORT=$(ls -t logs/json/pipeline_report_*.json 2>/dev/null | head -1)
if [ -z "$REPORT" ]; then
  log_echo "ERROR: No pipeline report found in logs/json/"
  exit 1
fi
log_echo "  Report: $REPORT"

# Step 2: Validate
log_echo ""
log_echo "--- Step 2: Running validation ---"
run_logged python scripts/validate_pipeline.py "$REPORT" --local-pdfs

# Step 3: Error analysis
log_echo ""
log_echo "--- Step 3: Analyzing errors ---"
run_logged python scripts/tuning/analyze_errors.py "$REPORT" --local-pdfs

# Step 4: Calibrate threshold
log_echo ""
log_echo "--- Step 4: Calibrating threshold ---"
# Find the latest score distribution
SCORE_DIST=$(ls -t logs/tuning/score_distribution_*.csv 2>/dev/null | head -1)
if [ -n "$SCORE_DIST" ]; then
  run_logged python scripts/tuning/calibrate_threshold.py "$SCORE_DIST"
else
  log_echo "  No score distribution CSV found, skipping calibration"
fi

# Step 5: Track run
log_echo ""
log_echo "--- Step 5: Tracking run ---"
run_logged python scripts/tuning/track_run.py \
  --pipeline-report "$REPORT" \
  --local-pdfs \
  --notes "${NOTES:-threshold=$THRESHOLD prompt=${PIPELINE_PROMPT_VERSION:-v3}}"

log_echo ""
log_echo "=== Experiment complete ==="
