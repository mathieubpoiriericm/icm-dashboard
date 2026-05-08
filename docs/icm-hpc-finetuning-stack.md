# ICM HPC Fine-Tuning Stack

Software stack used to QLoRA fine-tune Gemma 4 31B on the Paris Brain
Institute (ICM) high-performance computing cluster on a single-A100 (80 GB) system.

## Compute environment

Training jobs run on the `gpu-ampere` partition of the ICM HPC, scheduled
by SLURM 24.05.3 under account `debette-chabriat` and QoS `qos6`. The
partition spans two nodes (`sphpc-gpu05`, `sphpc-gpu06`) sharing eight
NVIDIA A100 80 GB PCIe accelerators (four per node). All training reported
here uses a single A100.

Per-node resources, measured on `sphpc-gpu05`:

| Resource | Value |
| --- | --- |
| OS | Rocky Linux 8.8 (Green Obsidian) |
| Kernel | 4.18.0-477.27.1.el8\_8.x86\_64 |
| CPU | 2 × Intel Xeon Gold 6330 @ 2.00 GHz (28 cores per socket, hyper-threading off; 4 NUMA nodes) |
| Host RAM | 1.0 TiB |
| Local scratch | `/dev/shm` (tmpfs, 504 GB, RAM-backed) |
| GPU | 1 × NVIDIA A100 80 GB PCIe (81920 MiB) |
| NVIDIA driver | 550.127.05 |

## Toolchain modules (Lmod)

The job script issues `module purge` followed by `module load` of the four
modules below. All are provided by the cluster's central Lmod tree.

| Module string | Underlying version | Role |
| --- | --- | --- |
| `CUDA/12.4` | CUDA Toolkit 12.4 (`nvcc` V12.4.131, built 2024-03-28) | CUDA toolkit (compiler, runtime headers) |
| `cudnn/9.8.0.87-11-pewru6u` | cuDNN 9.8.0.87 | Convolution / attention kernels |
| `gcc/12.4.0` | GCC 12.4.0 | C/C++ host compiler |
| `python/3.12` | CPython 3.12.8 | Python interpreter |

The trailing `-11-pewru6u` suffix on the cuDNN module is an EasyBuild/Lmod
identifier specific to this cluster; the underlying release (cuDNN 9.8.0.87)
is what a reader should match elsewhere. Loading these
four modules also pulls in `glibc/2.28-7rs64fv`, `gcc-runtime/8.5.0-4ihak4k`,
and `proxy/1.0.0` as transitive dependencies; these supply the runtime ABI
and the cluster's outbound HTTP proxy and require no user action.

## Python environment

Dependencies are managed by `uv` 0.11.11 in **project mode**. A
`pyproject.toml` (1.6 KB) and `uv.lock` (173 KB) are co-located on the lab
share at `/network/iss/debette/users/mathieu.poirier/`, alongside the
resolved virtual environment at `.venv/`. The lab share is used rather
than the NFS home directory because home is quota-limited and would not
fit `torch`'s vendored CUDA wheels.

Reproduction on a comparable cluster:

```bash
module load CUDA/12.4 cudnn/9.8.0.87-11-pewru6u gcc/12.4.0 python/3.12
cd /path/to/project
uv sync
```

`uv sync` resolves against `uv.lock`, producing an environment that
matches the table below byte-for-byte.

## Fine-tuning libraries

Resolved versions, captured from the active venv:

| Package | Version |
| --- | --- |
| `torch` | 2.6.0+cu124 |
| `unsloth` | 2026.5.2 |
| `trl` | 0.23.0 |
| `peft` | 0.19.1 |
| `bitsandbytes` | 0.49.2 |
| `transformers` | 5.5.0 |
| `datasets` | 4.3.0 |
| `accelerate` | 1.13.0 |
| `tokenizers` | 0.22.2 |

Three components carry methodological weight:

- **Unsloth** — fused-kernel QLoRA path. Provides `FastModel`, the unified
  loader for multimodal Gemma 4 checkpoints (text + vision + audio
  configs); the older `FastLanguageModel` rejects multimodal Gemma 4
  because of its composite `config.json`. With `finetune_language_layers=
  True`, Unsloth freezes the vision and audio towers and adapts only the
  language model's attention and MLP projections.
- **TRL** — supplies `SFTTrainer` and `train_on_responses_only`, the
  successor to `DataCollatorForCompletionOnlyLM`. Response-token masking
  uses Gemma 4's chat-template markers (`<|turn>user\n` and
  `<|turn>model\n`), distinct from Gemma 1–3 which use `<start_of_turn>`.
- **bitsandbytes** — NF4 4-bit base-model loading via `load_in_4bit=True`,
  combined with `optim="adamw_8bit"` for 8-bit optimizer state.

## Compatibility and runtime notes

Three properties of this stack affect replication on comparable clusters:

- **Vendored CUDA shared libraries.** `torch 2.6.0+cu124` ships its own
  copies of NCCL, cuBLAS, cuDNN, and related libraries under
  `site-packages/nvidia/*/lib`. Because `module purge` clears
  `LD_LIBRARY_PATH` and `module load CUDA/12.4` does not re-add the
  vendored paths, runs must extend `LD_LIBRARY_PATH` with each
  `nvidia/*/lib` directory after activating the venv. Without this,
  `import torch` fails to dlopen the libraries.
- **Hugging Face offline mode.** Compute nodes have no outbound network
  to `huggingface.co`. Models and tokenizers are pre-staged on the lab
  share under `$HF_HOME=/network/iss/debette/.../hf-cache/huggingface`;
  runs set `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, and
  `HF_DATASETS_OFFLINE=1` to disable network probes during model load.
- **NCCL configuration.** Single-node training sets `NCCL_P2P_DISABLE=0`
  (peer-to-peer over NVLink in the optional 2-GPU variant) and
  `NCCL_IB_DISABLE=1` (InfiniBand off; not required within a single node).

## Rationale

Methods-section justification of the principal choices:

- **Single A100 over a 2-GPU split.** At QLoRA, the 31B base, adapter
  parameters, and activations fit in 80 GB with margin. The 2-GPU NV12
  pair yields ~1.85× wall-clock at 2× billing — single-GPU is preferred
  for hyperparameter sweeps where N parallel 1-GPU jobs beat N/2 2-GPU
  jobs.
- **Unsloth over plain PEFT/TRL.** 1.5–2× faster QLoRA training via fused
  kernels and 4-bit-aware optimizer state, with first-class Gemma 4
  multimodal support absent in the upstream loader.
- **QLoRA (NF4 + LoRA) over full-precision LoRA.** A 4× memory reduction
  on the base weights brings 31B into reach on a single 80 GB device.
- **`uv` over `pip`.** Lockfile-pinned reproducibility (`uv.lock`) and
  faster resolution and install on the lab-share filesystem.
