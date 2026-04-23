#!/usr/bin/env bash
# Fuse LoRA adapter into base and convert to GGUF for Ollama.
#
# Usage:
#   ./scripts/finetune/fuse_and_convert.sh \
#       <adapter_dir> <fused_dir> <gguf_path>
#
# Requires:
#   - pip install "mlx-lm[train]"
#   - llama.cpp cloned at $LLAMA_CPP_DIR (default $HOME/src/llama.cpp)
#
# Rationale: mlx_lm.fuse --export-gguf supports only Mistral/Mixtral/Llama,
# not Gemma. So we fuse to safetensors, then convert with llama.cpp.
set -euo pipefail

ADAPTER_DIR="${1:?adapter dir required}"
FUSED_DIR="${2:?fused output dir required}"
GGUF_PATH="${3:?gguf output path required}"
MODEL="${MLX_LM_BASE_MODEL:-mlx-community/gemma-4-e4b-it-4bit}"
LLAMA_CPP="${LLAMA_CPP_DIR:-$HOME/src/llama.cpp}"

if [ ! -d "$LLAMA_CPP" ]; then
  echo "ERROR: llama.cpp not found at $LLAMA_CPP." >&2
  echo "Clone it:" >&2
  echo "  git clone https://github.com/ggml-org/llama.cpp $LLAMA_CPP" >&2
  exit 1
fi

# Step 1: fuse adapter into base (safetensors output).
mkdir -p "$(dirname "$FUSED_DIR")"
mlx_lm.fuse \
  --model "$MODEL" \
  --adapter-path "$ADAPTER_DIR" \
  --save-path "$FUSED_DIR"

# Step 2: safetensors -> GGUF Q4_K_M via llama.cpp.
mkdir -p "$(dirname "$GGUF_PATH")"
python "$LLAMA_CPP/convert_hf_to_gguf.py" \
  "$FUSED_DIR" \
  --outfile "$GGUF_PATH" \
  --outtype q4_k_m

echo "GGUF written: $GGUF_PATH"
ls -lh "$GGUF_PATH"
