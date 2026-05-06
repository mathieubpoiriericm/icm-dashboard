#!/bin/bash
# Single A100 80GB QLoRA training of Gemma 4 31B on the ICM HPC.
# Submit from the project root (paths below are relative to $SLURM_SUBMIT_DIR).
#
# 2-GPU variant (NV12 pair, ~1.85x wall-clock speedup): switch to TRL+PEFT
# and replace --gres=gpu:A100:1 with :2, --cpus-per-task=14 with 28,
# --mem=200G with 400G, and the launch line with:
#   srun --cpu-bind=cores --mem-bind=local \
#     torchrun --standalone --nproc_per_node=2 \
#       scripts/finetune/train_trl.py <same args>
# Verify NV12 pair allocation post-allocation with `nvidia-smi topo -m`;
# add --gres-flags=enforce-binding if a cross-pair allocation ever happens.

#SBATCH --job-name=gemma31b-qlora
#SBATCH --account=debette-chabriat
#SBATCH --partition=gpu-ampere
#SBATCH --qos=m120_c1
#SBATCH --time=08:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:A100:1
#SBATCH --cpus-per-task=14
#SBATCH --mem=200G
#SBATCH --signal=B:USR1@120
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail
mkdir -p logs

# ---- env ----
module purge
module load CUDA/12.4 cudnn/9.8.0.87-11-pewru6u gcc/12.4.0 python/3.12
source ~/venvs/gemma-finetune/bin/activate

export HF_HOME=/network/iss/debette/mathieu.poirier/hf-cache/huggingface
# Cluster has no outbound to huggingface.co — model is pre-staged on lab share.
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1

export TOKENIZERS_PARALLELISM=true
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export PYTHONUNBUFFERED=1

# Single-node NCCL: P2P over NVLink (matters in the 2-GPU variant), no IB
export NCCL_P2P_DISABLE=0
export NCCL_IB_DISABLE=1
export NCCL_DEBUG=WARN

ulimit -n 65536

# ---- stage data into /dev/shm (504 GB tmpfs, RAM-backed) ----
STAGE=/dev/shm/$USER/$SLURM_JOB_ID
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT
cp "$SLURM_SUBMIT_DIR/data/finetune/svd_v1/"*.jsonl "$STAGE/"

# ---- graceful checkpoint on USR1 (sent 120 s before time limit) ----
checkpoint_handler() {
  echo "[$(date -Iseconds)] USR1 received — signaling trainer to checkpoint+exit"
  kill -USR1 "$TRAIN_PID" 2>/dev/null || true
}
trap checkpoint_handler USR1

# ---- launch ----
srun --cpu-bind=cores --mem-bind=local \
  python -u scripts/finetune/train_unsloth.py \
    --base-model "unsloth/gemma-4-31b-it-bnb-4bit" \
    --train-jsonl  "$STAGE/train.jsonl" \
    --valid-jsonl  "$STAGE/valid.jsonl" \
    --output-dir   "models/lora_adapters/svd_v1_31b/" \
    --max-seq-length 8192 \
    --per-device-batch 2 \
    --grad-accum 4 \
    --learning-rate 1e-5 \
    --max-steps 600 \
    --lora-rank 16 \
    --warmup-ratio 0.05 \
    --eval-steps 100 --save-steps 200 \
  &
TRAIN_PID=$!
wait "$TRAIN_PID"
