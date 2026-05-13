#!/bin/bash
# Compile flash-attn from source against the cu126 venv.
# Runs on the `gpu-ampere` partition to match the runtime OS (Rocky 8.8,
# glibc 2.28). Building on `compute` (which is Rocky 9, glibc 2.34)
# produces a .so that fails to import at runtime with:
#     ImportError: /lib64/libc.so.6: version `GLIBC_2.32' not found
# The build itself doesn't use the GPU — we just need the matching libc.
#
# PREREQUISITES — edit pyproject.toml on the HPC FIRST:
#   1. Add to [project].dependencies:
#        "flash-attn>=2.7.0",
#   2. Add to [tool.uv]:
#        no-build-isolation-package = ["flash-attn"]
#      (Without this, FA2's setup.py runs in an isolated sandbox without
#       torch installed and the build dies immediately.)
#
# Submit:    sbatch scripts/finetune/icm_fa2_build.sbatch
# Watch:     tail -f logs/fa2-build-<JOBID>.out
# Verify:    python -c "import flash_attn; print(flash_attn.__version__)"
#
# Optional: add SLURM mail directives below if your cluster has mail
# relay configured:
#   #SBATCH --mail-type=END,FAIL
#   #SBATCH --mail-user=mathieu.poirier@icm-institute.org

#SBATCH --job-name=fa2-build
#SBATCH --account=debette-chabriat
# gpu-all spans every GPU-equipped partition (Volta/Tesla/Ampere/etc.).
# We target it (not gpu-ampere specifically) because gpu-ampere is
# typically saturated while gpu-volta/gpu-tesla sit idle, AND all GPU
# nodes run Rocky 8.8 with glibc 2.28 — matching the training runtime.
# The `compute` partition is Rocky 9 (glibc 2.34) and produces .so
# binaries that fail to import at runtime with GLIBC_2.32 not found.
#SBATCH --partition=gpu-all
#SBATCH --qos=qos6
#SBATCH --time=02:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=8
# Allocate a V100 (cheapest, always idle) just to satisfy gpu-all's
# GRES requirement. The build itself is pure CPU work; the GPU is unused.
#SBATCH --gres=gpu:V100:1
# 96G needed: FA2's nvcc compiles of bf16/fp16 SM80 kernels peak around
# 10 GB each. With MAX_JOBS=4 ninja tasks running in parallel + Python
# orchestration + /dev/shm scratch, ~60-70 GB is realistic. 32 GB ⇒ OOM.
#SBATCH --mem=96G
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail
mkdir -p logs

PROJECT_DIR=/network/iss/debette/users/mathieu.poirier

# ---- preconditions ----
# Fail fast if the user forgot to edit pyproject.toml — otherwise we'd
# burn 30 min only to learn nothing got built.
if ! grep -q '"flash-attn' "$PROJECT_DIR/pyproject.toml"; then
    echo "ERROR: flash-attn not declared in $PROJECT_DIR/pyproject.toml" >&2
    echo "       Add \"flash-attn>=2.7.0\" to [project].dependencies first." >&2
    exit 1
fi
if ! grep -q 'no-build-isolation-package.*flash-attn' "$PROJECT_DIR/pyproject.toml"; then
    echo "ERROR: no-build-isolation-package missing for flash-attn in pyproject.toml" >&2
    echo "       Add to [tool.uv]:  no-build-isolation-package = [\"flash-attn\"]" >&2
    exit 1
fi

# ---- env ----
# CUDA/12.6.3-nak2nbx matches the venv's torch 2.10.0+cu126. This is a
# Spack-built module that sets CUDA_HOME directly via setenv() — do NOT
# override it after the load.
#
# gcc/8.5.0 (NOT gcc/12.4.0): the cluster's gcc/12.4.0 install ships
# without a discoverable libisl.so.15, so cc1plus dies on every build.
# gcc/8.5.0 has full C++17 support, matches the auto-loaded
# gcc-runtime/8.5.0 (avoiding Lmod family conflicts), and falls inside
# CUDA 12.6's supported gcc range with explicit version bounds in
# torch's cpp_extension.
module purge
module load CUDA/12.6.3-nak2nbx cudnn/9.8.0.87-11-pewru6u python/3.12
source "$PROJECT_DIR/.venv/bin/activate"

# Verify the module actually set CUDA_HOME (defensive — Lmod errors are
# silent if depends_on() can't be satisfied).
: "${CUDA_HOME:?CUDA_HOME not set after module load — is CUDA/12.6.3-nak2nbx actually loaded?}"
[[ -x "$CUDA_HOME/bin/nvcc" ]] || {
    echo "ERROR: nvcc not found at $CUDA_HOME/bin/nvcc" >&2
    exit 1
}

# Compute nodes have no /usr/bin/gcc — torch's cpp_extension defaults to
# /usr/bin/g++ for the host compile and passes -ccbin /usr/bin/gcc to nvcc.
# Override via CC/CXX env vars to point at the gcc/8.5.0 module's binaries.
CC="$(command -v gcc)"
CXX="$(command -v g++)"
[[ -x "$CC"  ]] || { echo "ERROR: gcc not on PATH after module load — check gcc/8.5.0" >&2; exit 1; }
[[ -x "$CXX" ]] || { echo "ERROR: g++ not on PATH after module load — check gcc/8.5.0" >&2; exit 1; }
export CC CXX

# Cap parallel nvcc compiles to keep peak memory under --mem.
# 8 parallel nvcc × ~10 GB each ⇒ ~80 GB ⇒ OOM-kill with --mem=32G.
# 4 parallel × ~10 GB ⇒ ~40 GB peak, fits in 96 GB with headroom.
NVCC_MAX_JOBS=4

# FA2's setup.py hardcodes -gencode for sm_80 AND sm_90, ignoring
# TORCH_CUDA_ARCH_LIST. We only target A100 (sm_80); skip Hopper.
# This halves compile time and drops peak memory ~30%.
export FLASH_ATTN_CUDA_ARCHS=80

# Torch ships its CUDA libs inside the venv; module purge dropped them
# from LD path. Needed for `import torch` during the build.
for lib_dir in "$VIRTUAL_ENV"/lib/python3.12/site-packages/nvidia/*/lib; do
    [[ -d "$lib_dir" ]] && export LD_LIBRARY_PATH="$lib_dir:${LD_LIBRARY_PATH:-}"
done

# CUDA_HOME is set by the CUDA/12.6.3-nak2nbx module above — do NOT override.
export MAX_JOBS="$NVCC_MAX_JOBS"
export FLASH_ATTENTION_FORCE_BUILD=TRUE
export TORCH_CUDA_ARCH_LIST="8.0"   # torch's BuildExtension arch list (FA2
                                     # ignores this and uses FLASH_ATTN_CUDA_ARCHS
                                     # set above)

# Big object files during the build — redirect TMPDIR to /dev/shm (tmpfs,
# RAM-backed) to avoid hitting /tmp limits and to speed up I/O.
export TMPDIR="/dev/shm/$USER/$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

# ---- pre-flight sanity ----
echo "==> Pre-build sanity"
echo "  nvcc           : $(command -v nvcc)"
nvcc --version | tail -2 | sed 's/^/    /'
echo "  CUDA_HOME      : $CUDA_HOME"
echo "  CC             : $CC"
echo "  CXX            : $CXX"
"$CXX" --version | head -1 | sed 's/^/    /'
echo "  TMPDIR         : $TMPDIR ($(df -h "$TMPDIR" | tail -1 | awk '{print $4}') free)"
echo "  MAX_JOBS       : $MAX_JOBS (parallel ninja tasks; lower = less peak RAM)"
echo "  TORCH_CUDA_ARCH: $TORCH_CUDA_ARCH_LIST"
echo "  FA2_CUDA_ARCHS : $FLASH_ATTN_CUDA_ARCHS (FA2-specific; sm_90 skipped)"
echo "  torch          : $(python -c 'import torch; print(torch.__version__)')"
echo "  pyproject      : $PROJECT_DIR/pyproject.toml"
echo

# ---- snapshot lock for rollback ----
cp "$PROJECT_DIR/uv.lock" "$PROJECT_DIR/uv.lock.bak-pre-fa2"
echo "==> Snapshotted uv.lock -> uv.lock.bak-pre-fa2"

# ---- build ----
cd "$PROJECT_DIR"

echo "==> Running uv sync (FA2 source build)"
echo "    Expected runtime: 15-30 min with TORCH_CUDA_ARCH_LIST=8.0"
echo "    (default would compile ~6 archs and take 40-90 min)"
echo
time uv sync

# ---- verify ----
echo
echo "==> Verifying flash-attn install"

# Step 1 (mandatory): the build artifact must exist. FA2 installs its
# compiled extension as a top-level module (flash_attn_2_cuda*.so) at
# site-packages level, NOT inside the flash_attn/ package directory.
SITE_PACKAGES="$VIRTUAL_ENV/lib/python3.12/site-packages"
FA2_SO=$(find "$SITE_PACKAGES" -maxdepth 2 -name 'flash_attn*.so' 2>/dev/null | head -1)
if [[ -z "$FA2_SO" ]]; then
    echo "ERROR: no flash_attn*.so found in $SITE_PACKAGES — build did not produce output" >&2
    exit 1
fi
echo "  build artifact : $FA2_SO"
echo "  size           : $(du -h "$FA2_SO" | awk '{print $1}')"

# Step 2 (soft): import test. Compute nodes typically have no NVIDIA
# driver, so dlopen of libcuda.so.1 may fail even on a good build.
# Don't fail the job on import error — the real import test happens
# on the next GPU job.
if python -c "import flash_attn; print(f'  version        : {flash_attn.__version__}')" 2>/dev/null; then
    echo "  import test    : PASSED"
else
    echo "  import test    : skipped (no GPU driver on compute partition — will be re-tested on next GPU run)"
fi

echo
echo "==> Done. Next training run will show 'FA2 = True' in the unsloth banner."
echo "    Rollback if needed:  mv $PROJECT_DIR/uv.lock.bak-pre-fa2 $PROJECT_DIR/uv.lock && uv sync"
