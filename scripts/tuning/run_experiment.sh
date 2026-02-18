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

THRESHOLD="${1:-0.7}"
NOTES="${2:-}"
PDF_PATH="${3:-pipeline/test_data/37069360.pdf}"

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

# Capture all console output so it can be embedded in the JSON report later.
LOG_FILE=$(mktemp "${TMPDIR:-/tmp}/experiment_log.XXXXXX")
trap 'rm -f "$LOG_FILE"' EXIT
exec 3>&1 4>&2
exec > >(tee "$LOG_FILE") 2>&1

echo "=== Tuning Experiment ==="
echo "  Threshold:      $THRESHOLD"
echo "  PDF:            $PDF_PATH"
echo "  Prompt version: ${PIPELINE_PROMPT_VERSION:-v3}"
echo "  Notes:          ${NOTES:-<none>}"
echo ""

# Step 1: Extract
echo "--- Step 1: Running pipeline extraction ---"
PIPELINE_CONFIDENCE_THRESHOLD="$THRESHOLD" \
  python pipeline/main.py --local-pdfs "$PDF_PATH"

# Find the latest report
REPORT=$(ls -t logs/json/pipeline_report_*.json 2>/dev/null | head -1)
if [ -z "$REPORT" ]; then
  echo "ERROR: No pipeline report found in logs/json/"
  exit 1
fi
echo "  Report: $REPORT"

# Step 2: Validate
echo ""
echo "--- Step 2: Running validation ---"
python scripts/validate_pipeline.py "$REPORT" --local-pdfs

# Step 3: Error analysis
echo ""
echo "--- Step 3: Analyzing errors ---"
python scripts/tuning/analyze_errors.py "$REPORT" --local-pdfs

# Step 4: Calibrate threshold
echo ""
echo "--- Step 4: Calibrating threshold ---"
# Find the latest score distribution
SCORE_DIST=$(ls -t logs/tuning/score_distribution_*.csv 2>/dev/null | head -1)
if [ -n "$SCORE_DIST" ]; then
  python scripts/tuning/calibrate_threshold.py "$SCORE_DIST"
else
  echo "  No score distribution CSV found, skipping calibration"
fi

# Step 5: Track run
echo ""
echo "--- Step 5: Tracking run ---"
python scripts/tuning/track_run.py \
  --pipeline-report "$REPORT" \
  --local-pdfs \
  --notes "${NOTES:-threshold=$THRESHOLD prompt=${PIPELINE_PROMPT_VERSION:-v3}}"

echo ""
echo "=== Experiment complete ==="

# Stop tee so the log file is fully flushed before we read it.
exec 1>&3 2>&4 3>&- 4>&-
sleep 0.1

# Embed the captured console output into the pipeline JSON report.
python3 -c '
import json, sys
report_path, log_path = sys.argv[1], sys.argv[2]
with open(report_path, encoding="utf-8") as f:
    report = json.load(f)
with open(log_path, encoding="utf-8") as f:
    report["experiment_log"] = f.read().splitlines()
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
    f.write("\n")
' "$REPORT" "$LOG_FILE"

echo "Console log embedded in: $REPORT"
