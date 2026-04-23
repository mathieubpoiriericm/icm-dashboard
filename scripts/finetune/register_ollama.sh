#!/usr/bin/env bash
# Register a GGUF model with local Ollama.
#
# Usage:
#   ./scripts/finetune/register_ollama.sh <gguf_path> [tag]
#
# The system prompt is NOT baked into the Modelfile — the pipeline injects
# pipeline/prompts.py::ollama_v1 at serving time.
set -euo pipefail

GGUF_PATH="${1:?gguf path required}"
TAG="${2:-svd-gemma:latest}"
MODELFILE="$(mktemp -t modelfile.XXXXXX)"
trap 'rm -f "$MODELFILE"' EXIT

cat > "$MODELFILE" <<EOF
FROM $(realpath "$GGUF_PATH")
PARAMETER num_ctx 65536
PARAMETER temperature 0
EOF

ollama create "$TAG" -f "$MODELFILE"
echo "Registered: $TAG"
ollama run "$TAG" "Hello, respond with OK" | head -1
