# Local LLM: Gemma 4 via Ollama + MLX-LM fine-tuning

An alternative extraction provider alongside the default Anthropic Claude path.
Run the pipeline locally for free; optionally fine-tune Gemma 4 on past
extractions to specialize it for cerebral small vessel disease (cSVD)
literature.

Claude via the Anthropic API remains the default. This path is for cost-free
batch runs and domain-specialized experiments. Switch providers freely — no
DB migrations, no schema changes.

## Prerequisites

Apple Silicon Mac (M1-M4). Linux + NVIDIA GPU works for serving but not for
the MLX-LM training scripts (Darwin arm64 only).

1. **Ollama** — install from <https://ollama.com/download>. Verify:

   ```bash
   ollama --version
   ollama serve &    # if not already running as a launchd service
   ```

2. **Gemma 4 base model** (for serving the non-fine-tuned variant):

   ```bash
   ollama pull gemma4:e4b
   ```

3. **Python deps** — already in `requirements.txt`:

   - `ollama>=0.4.0` (platform-agnostic; Linux can serve against a remote
     Ollama host too)
   - `mlx-lm[train]>=0.20.0` (Darwin arm64 only, enforced by environment
     marker — Linux pip install skips this line automatically)

   Run `pip install -r requirements.txt` if you haven't since updating.

4. **llama.cpp** for GGUF conversion. `mlx_lm.fuse --export-gguf` supports
   only Mistral/Mixtral/Llama, so Gemma conversion goes through
   `llama.cpp/convert_hf_to_gguf.py`:

   ```bash
   git clone https://github.com/ggml-org/llama.cpp ~/src/llama.cpp
   # No build needed — we only use the Python converter.
   ```

   Override the location via `LLAMA_CPP_DIR` if cloned elsewhere.

## Serving base Gemma 4 (no fine-tuning)

Either via env var:

```bash
PIPELINE_LLM_PROVIDER=ollama python pipeline/main.py --pubmed --days-back 7
```

Or via the CLI flag:

```bash
python pipeline/main.py --pubmed --days-back 7 --llm-provider ollama
```

What happens: same pipeline flow, but extraction calls hit
`http://localhost:11434` instead of the Anthropic API. The run report shows
$0 cost. Extraction quality is measurably lower than Claude on cSVD papers —
fine-tuning closes that gap.

Override the model tag for a specific run:

```bash
python pipeline/main.py --llm-provider ollama --ollama-model svd-gemma:v1
```

Override the Ollama host (e.g., Gemma running on a remote Mac on the LAN):

```bash
PIPELINE_OLLAMA_HOST=http://192.168.1.42:11434 python pipeline/main.py --llm-provider ollama
```

## Fine-tuning loop

The workflow is five scripts, each independently runnable.

### 1. Build the training dataset

```bash
python scripts/finetune/build_dataset.py \
    --reports-glob "logs/json/pipeline_report_*.json" \
    --pdf-dir data/test_data/pdf/ \
    --min-confidence 0.7 \
    --max-papers 800 \
    --valid-frac 0.1 \
    --max-paper-chars 20000 \
    --out-dir data/finetune/svd_v1/
```

Reads past Claude extractions from pipeline report JSON files, pairs each
paper with its PDF text (parsed via `pipeline/pdf_retrieval.py`), filters out
low-confidence genes and the gold-standard PMIDs, and writes MLX-LM chat
JSONL to `train.jsonl` + `valid.jsonl` in the out-dir.

To exclude gold-standard PMIDs (prevent evaluation leakage), create a text
file with one PMID per line and pass `--gold-pmids-file path/to/file`.

### 2. LoRA fine-tune (QLoRA via MLX-LM)

```bash
./scripts/finetune/finetune_mlx.sh data/finetune/svd_v1/
```

Outputs the adapter to `models/lora_adapters/svd_v1/` and logs training loss
to `models/lora_adapters/svd_v1/training.log`.

Override the base model or adapter path:

```bash
MLX_LM_BASE_MODEL=mlx-community/gemma-4-e4b-it-4bit \
    ./scripts/finetune/finetune_mlx.sh data/finetune/svd_v1/ models/lora_adapters/exp_2/
```

The script uses these settings (edit the script to change): `--num-layers 16
--batch-size 2 --iters 600 --learning-rate 1e-5 --grad-checkpoint
--mask-prompt`. Tuned for 16 GB unified memory with headroom.

### 3. Fuse the adapter, convert to GGUF

```bash
./scripts/finetune/fuse_and_convert.sh \
    models/lora_adapters/svd_v1/ \
    models/fused/svd_v1/ \
    models/gguf/svd-gemma-v1-Q4_K_M.gguf
```

Two steps: `mlx_lm.fuse` writes fused safetensors, then
`llama.cpp/convert_hf_to_gguf.py` writes the Q4_K_M GGUF.

### 4. Register with Ollama

```bash
./scripts/finetune/register_ollama.sh \
    models/gguf/svd-gemma-v1-Q4_K_M.gguf \
    svd-gemma:v1
```

Writes a Modelfile pointing `FROM` the GGUF path and sets `num_ctx=65536` /
`temperature=0`. The system prompt is NOT baked in — the pipeline injects
`ollama_v1` (from `pipeline/prompts.py`) at serving time, so prompt
revisions stay in version control.

Verify:

```bash
ollama list   # should show svd-gemma:v1
```

### 5. Evaluate on the gold set

The evaluation CLI takes pre-produced pipeline reports (one per config).
Produce them first:

```bash
# (a) Claude baseline
python pipeline/main.py --pmids <gold-pmids-file> --skip-validation

# (b) base Gemma 4
python pipeline/main.py --pmids <gold-pmids-file> --skip-validation \
    --llm-provider ollama --ollama-model gemma4:e4b

# (c) fine-tuned
python pipeline/main.py --pmids <gold-pmids-file> --skip-validation \
    --llm-provider ollama --ollama-model svd-gemma:v1
```

Each run writes a JSON report to `logs/json/pipeline_report_*.json`. Then:

```bash
python scripts/finetune/eval_finetuned.py \
    --report claude-baseline:logs/json/<claude-report>.json \
    --report gemma4-base:logs/json/<gemma-base-report>.json \
    --report svd-gemma:v1:logs/json/<gemma-ft-report>.json
```

The tool reuses `scripts/validate_pipeline.py`'s `parse_pipeline_json` →
`compare_all` → `compute_scores` chain, and prints a side-by-side comparison:

```text
Config                     P       R       F1   MeanGene   Composite
------------------------------------------------------------
claude-baseline         0.XXX   0.XXX   0.XXX      0.XXX       0.XXX
gemma4-base             0.XXX   0.XXX   0.XXX      0.XXX       0.XXX
svd-gemma:v1            0.XXX   0.XXX   0.XXX      0.XXX       0.XXX
```

`--recommend --tag svd-gemma:v1` prints the recommended three configs and
the commands to produce reports for each.

## Interpreting the results

**Decision gate:** only flip `PIPELINE_LLM_PROVIDER=ollama` as the working
default once `svd-gemma:v1` reaches within ~5 F1 points of Claude on the
gold set. Until then, use Ollama ad-hoc for free batch runs (development,
backfills, re-runs), but keep Claude as the production default.

Expected first-iteration behavior:

- `gemma4-base` will be dramatically worse than Claude — that's the
  specialization gap.
- `svd-gemma:v1` after a single fine-tune on ~20 papers will improve over
  `gemma4-base` but won't match Claude. That's the data-size gap; each
  additional ~100-200 papers of gold/silver training data measurably closes
  it.

## Troubleshooting

### `Ollama is not reachable at http://localhost:11434`

Ollama isn't running. Start it: `ollama serve &` (or check
`launchctl list | grep ollama` if you installed the launchd service). The
pipeline fails fast on provider init rather than halfway through a run.

### `convert_hf_to_gguf.py: error: unsupported architecture`

Your `llama.cpp` checkout is too old. Pull latest:

```bash
cd ~/src/llama.cpp && git pull
```

Gemma 4 support was added after the initial release. If the error persists
after a fresh pull, check the `ggml-org/llama.cpp` issue tracker.

### MLX-LM OOM during training

Shrink memory usage in `finetune_mlx.sh`:

- `--batch-size 1` (from 2)
- `--num-layers 8` (from 16)

And in `build_dataset.py`:

- `--max-paper-chars 10000` (from 20000)

On 16 GB unified memory, these combined settings are typically viable with
the 4-bit base model.

### Fine-tuned model produces garbled JSON

Check two things:

1. `--mask-prompt` was passed to `mlx_lm.lora` (so loss is computed only on
   the JSON assistant turn, not on prompt tokens). It's in the shipped
   `finetune_mlx.sh`; don't remove it.
2. Training dataset used `build_extraction_messages(..., provider="ollama",
   prompt_version="ollama_v1")`. `build_dataset.py` does this correctly;
   don't hand-edit the JSONL.

### Cost tracking shows non-zero for Ollama runs

This shouldn't happen — `MODEL_PRICING` has no entry for Ollama tags, so
the reporter returns 0. If you see cost > 0, the provider field wasn't
passed through; double-check `PIPELINE_LLM_PROVIDER`, `--llm-provider`,
and the UI selector on Configure & Run.

### Ollama is slow to respond on the first call

The model loads into VRAM on first use. `keep_alive=30m` (set by the
pipeline) keeps it resident; subsequent calls in the same run should be
fast.

## See also

- `docs/python-etl-pipeline.md` — overall pipeline architecture.
- `CLAUDE.md` — project conventions and environment variables.
- `docs/superpowers/specs/2026-04-22-gemma-local-llm-option-design.md` — design rationale.
- `docs/superpowers/plans/2026-04-22-gemma-local-llm-option.md` — implementation plan.
