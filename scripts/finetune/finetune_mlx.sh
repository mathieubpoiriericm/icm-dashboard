#!/usr/bin/env bash
# Fine-tune Gemma 4 E4B (4-bit) with MLX-LM LoRA/QLoRA.
#
# Usage:
#   ./scripts/finetune/finetune_mlx.sh <data_dir> [adapter_dir]
#
# Requires: pip install "mlx-lm[train]"
#
# data_dir must contain train.jsonl and valid.jsonl in MLX-LM chat format
# (produced by scripts/finetune/build_dataset.py).
set -euo pipefail

DATA_DIR="${1:?data dir (containing train.jsonl, valid.jsonl) required}"
ADAPTER_DIR="${2:-models/lora_adapters/svd_v1/}"
MODEL="${MLX_LM_BASE_MODEL:-mlx-community/gemma-4-e4b-it-4bit}"

mkdir -p "$ADAPTER_DIR"

mlx_lm.lora \
  --model "$MODEL" \
  --train \
  --data "$DATA_DIR" \
  --adapter-path "$ADAPTER_DIR" \
  --fine-tune-type lora \
  --num-layers 16 \
  --batch-size 2 \
  --iters 600 \
  --learning-rate 1e-5 \
  --grad-checkpoint \
  --mask-prompt \
  2>&1 | tee "$ADAPTER_DIR/training.log"

echo "Training complete. Adapter + log at: $ADAPTER_DIR"
