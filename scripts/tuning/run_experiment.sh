#!/usr/bin/env bash
# Run a full tuning experiment: extract → validate → analyze → calibrate → track.
#
# Usage:
#   ./scripts/tuning/run_experiment.sh [options] [threshold] [notes] [pdf_path]
#
# Options:
#   --fast         Low effort + 16K max_tokens for ~3x faster iteration.
#   --repeats N    Run the same config N times to measure variance.
#
# Arguments (positional, all optional):
#   threshold      Confidence threshold for extraction (default: 0.70).
#   notes          Free-text label stored in the run tracker.
#   pdf_path       Path to a PDF or directory (default: pipeline/test_data/).
#
# Defaults:
#   Model: claude-sonnet-4-6  |  Effort: high  |  Max tokens: 64000
#   Override any default via env vars: PIPELINE_LLM_MODEL, PIPELINE_LLM_EFFORT,
#   PIPELINE_LLM_MAX_TOKENS, PIPELINE_PROMPT_VERSION, PIPELINE_CONFIDENCE_THRESHOLD.
#
# Examples:
#   ./scripts/tuning/run_experiment.sh                        # all defaults
#   ./scripts/tuning/run_experiment.sh --fast 0.70 "quick"    # low effort, 16K tokens
#   ./scripts/tuning/run_experiment.sh --repeats 3 0.70 "var" # 3 repeats for variance
#   ./scripts/tuning/run_experiment.sh 0.7 "v2" pipeline/test_data/36180795.pdf
#   PIPELINE_LLM_MODEL=claude-opus-4-6 ./scripts/tuning/run_experiment.sh  # use Opus

set -euo pipefail

# Parse flags
FAST_MODE=false
SKIP_VALIDATION_FLAG=""
REPEATS=1

while [ $# -gt 0 ]; do
  case "${1}" in
    --fast)
      FAST_MODE=true
      export PIPELINE_LLM_MODEL="${PIPELINE_LLM_MODEL:-claude-sonnet-4-6}"
      export PIPELINE_LLM_EFFORT="${PIPELINE_LLM_EFFORT:-low}"
      export PIPELINE_LLM_MAX_TOKENS="${PIPELINE_LLM_MAX_TOKENS:-16000}"
      shift
      ;;
    --repeats)
      REPEATS="${2:?--repeats requires a number}"
      shift 2
      ;;
    *)
      break
      ;;
  esac
done

# Default to Sonnet 4.6 (--fast still useful for low effort + reduced max_tokens).
export PIPELINE_LLM_MODEL="${PIPELINE_LLM_MODEL:-claude-sonnet-4-6}"

THRESHOLD="${1:-0.70}"
NOTES="${2:-}"
PDF_PATH="${3:-pipeline/test_data/}"

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

# Generate a run group ID when doing repeats
RUN_GROUP=""
if [ "$REPEATS" -gt 1 ]; then
  RUN_GROUP="grp_$(date +%Y%m%d_%H%M%S)"
fi

# Force rich/colorama to emit ANSI colors even though stdout is piped through tee.
export FORCE_COLOR=1

# Collect reports for summary stats across repeats
ALL_REPORTS=()

for ITER in $(seq 1 "$REPEATS"); do

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

# Helper: echo to both terminal and log file.
log_echo() {
  echo "$@" | tee -a "$LOG_FILE"
}

# Helper: run a command, sending its stdout+stderr to both terminal and log file.
run_logged() {
  "$@" 2>&1 | tee -a "$LOG_FILE"
}

if [ "$REPEATS" -gt 1 ]; then
  log_echo "=== Tuning Experiment (repeat $ITER/$REPEATS, group $RUN_GROUP) ==="
else
  log_echo "=== Tuning Experiment ==="
fi
MODEL_VERSION=$(echo "${PIPELINE_LLM_MODEL}" | sed -n 's/.*claude-\(opus\|sonnet\|haiku\)-\([0-9]*\)-\([0-9]*\).*/\2.\3/p')
MODEL_VERSION="${MODEL_VERSION:-unknown}"

log_echo "  Threshold:      $THRESHOLD"
log_echo "  PDF:            $PDF_PATH"
log_echo "  Prompt version: ${PIPELINE_PROMPT_VERSION:-v5}"
log_echo "  Model:          ${PIPELINE_LLM_MODEL}"
log_echo "  Model version:  $MODEL_VERSION"
log_echo "  Effort:         ${PIPELINE_LLM_EFFORT:-high}"
log_echo "  Max tokens:     ${PIPELINE_LLM_MAX_TOKENS:-64000}"
log_echo "  Fast mode:      $FAST_MODE"
log_echo "  Notes:          ${NOTES:-<none>}"
log_echo ""

# Step 1: Extract
log_echo "--- Step 1: Running pipeline extraction ---"
PIPELINE_CONFIDENCE_THRESHOLD="$THRESHOLD" \
  run_logged python pipeline/main.py --local-pdfs "$PDF_PATH" $SKIP_VALIDATION_FLAG

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
SCORE_DIST=$(ls -t logs/tuning/score_distributions/score_distribution_*.csv 2>/dev/null | head -1)
if [ -n "$SCORE_DIST" ]; then
  run_logged python scripts/tuning/calibrate_threshold.py "$SCORE_DIST"
else
  log_echo "  No score distribution CSV found, skipping calibration"
fi

# Step 5: Track run
log_echo ""
log_echo "--- Step 5: Tracking run ---"
TRACK_ARGS=(
  --pipeline-report "$REPORT"
  --local-pdfs
  --notes "${NOTES:-threshold=$THRESHOLD prompt=${PIPELINE_PROMPT_VERSION:-v5}}"
)
if [ -n "$RUN_GROUP" ]; then
  TRACK_ARGS+=(--run-group "$RUN_GROUP")
fi
run_logged python scripts/tuning/track_run.py "${TRACK_ARGS[@]}"

# Step 6: Plot
log_echo ""
log_echo "--- Step 6: Plotting tuning runs ---"
run_logged Rscript scripts/plot_tuning_runs.R

# Embed log and clean up for this iteration
embed_log

ALL_REPORTS+=("$REPORT")

log_echo ""
log_echo "=== Repeat $ITER/$REPEATS complete ==="

done  # end of repeats loop

# Print variance summary if multiple repeats
if [ "$REPEATS" -gt 1 ]; then
  echo ""
  echo "=== Variance Summary ($REPEATS repeats, group $RUN_GROUP) ==="
  python3 -c '
import csv, sys, statistics
from pathlib import Path

csv_path = Path("logs/tuning/tuning_runs.csv")
if not csv_path.exists():
    print("  No tuning_runs.csv found", file=sys.stderr)
    sys.exit(1)

group = sys.argv[1]
with open(csv_path, newline="", encoding="utf-8") as f:
    rows = [r for r in csv.DictReader(f) if r.get("run_group") == group]

if len(rows) < 2:
    print(f"  Only {len(rows)} row(s) found for group {group}, need >= 2 for variance")
    sys.exit(0)

metrics = ["precision", "recall", "f1", "f2", "composite_score"]
print(f"  Runs: {len(rows)}")
print(f"  {'Metric':<18} {'Mean':>8} {'Std':>8} {'Min':>8} {'Max':>8}")
print(f"  {'-'*18} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
for m in metrics:
    vals = []
    for r in rows:
        try:
            vals.append(float(r[m]))
        except (ValueError, KeyError):
            pass
    if len(vals) >= 2:
        mean = statistics.mean(vals)
        std = statistics.stdev(vals)
        lo, hi = min(vals), max(vals)
        print(f"  {m:<18} {mean:>8.4f} {std:>8.4f} {lo:>8.4f} {hi:>8.4f}")
    elif vals:
        print(f"  {m:<18} {vals[0]:>8.4f}      n/a      n/a      n/a")
' "$RUN_GROUP"
fi
