# HPC Pipeline App — Runtime Walkthrough

A chronological tour of what `pipeline_app_hpc/` actually does when you click
**Start vLLM**, then **Run Pipeline**, then **Stop vLLM**, with the failure
modes in between. Every step is anchored to a specific file and line so you
can jump from the prose into the code.

The companion design doc is
[`docs/superpowers/specs/2026-05-09-hpc-vllm-pipeline-stack-design.md`](superpowers/specs/2026-05-09-hpc-vllm-pipeline-stack-design.md);
the HPC stack itself (modules, vendored CUDA libs, NCCL config) is in
[`docs/icm-hpc-finetuning-stack.md`](icm-hpc-finetuning-stack.md). This
file is the missing middle layer: how the GUI on your Mac wires those two
worlds together.

## Contents

- [What's running where](#whats-running-where)
- [What exists before you click anything](#what-exists-before-you-click-anything)
- [Click "Start vLLM" → green "Ready" chip](#click-start-vllm--green-ready-chip)
- [Click "Run Pipeline" → results JSON](#click-run-pipeline--results-json)
- [Click "Stop vLLM" → IDLE](#click-stop-vllm--idle)
- [Failure modes](#failure-modes)
- [Manual verification cheatsheet](#manual-verification-cheatsheet)

## What's running where

Three machines are involved. Almost every confusing detail in the lifecycle
code exists because traffic has to bridge two of them at a time.

```text
┌────────────────────────────────────────┐
│ Your Mac                               │
│                                        │
│  NiceGUI app  http://127.0.0.1:8081    │
│       │                                │
│       ▼  asyncio subprocess spawn      │
│  ssh master  (one persistent process)  │
│       │                                │
│       │  Unix socket:                  │
│       │  ~/.cache/csvd-hpc/icm-hpc.sock│
│       │                                │
│       ▼  multiplexed channels          │
│  ssh -L 30800:<gpu-node>:NNNNN ────────┼──┐
└────────────────────────────────────────┘  │
                       │ TCP/22             │
                       ▼                    │
┌─────────────────────────────────────────┐ │
│ ICM login node  sphpc-login02           │ │
│                                         │ │
│  sshd ── bash -lc "sbatch …"            │ │
│                                         │ │
│  SLURM controller (squeue, sacct,       │ │
│                    scancel)             │ │
└─────────────────────────────────────────┘ │
                       │                    │
                       │ SLURM allocation   │
                       ▼                    │
┌─────────────────────────────────────────┐ │
│ Compute node (e.g. gpu-ampere-NN)       │ │
│  ┌───────────────────────────────────┐  │ │
│  │ srun: vllm serve … --port NNNNN   │◄─┼─┘
│  │   gemma-4-31b-it (4-bit BnB)      │  │
│  │   on 1× A100                      │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

The single SSH master handles everything: heredoc writes, sbatch submission,
squeue polling, log tailing, the `-L` port forward, and `scancel` on
shutdown. Multiplexing means each operation costs a few milliseconds (one
control-channel round-trip) instead of a full TCP+SSH handshake.

## What exists before you click anything

Some state has to be in place before the app can do anything useful.

**On your Mac:**

- `~/.ssh/config` defines the alias `icm-hpc` pointing at
  `sphpc-login02.icm-institute.org` (key auth, MFA if your shell asks for
  it). The app shells out to `ssh icm-hpc …` and trusts that this resolves.
- `pipeline_app_hpc/config.json` (loaded into
  [`HpcAppConfig`](../pipeline_app_hpc/config.py) at app start) holds every
  knob: SSH alias, remote workdir, vLLM model, SLURM account/partition/qos,
  the local tunnel port (`vllm_local_port=30800`), and the readiness timeout
  (`vllm_readiness_timeout=900` seconds).
- A Python venv with NiceGUI + httpx so `python -m pipeline_app_hpc.main`
  can boot.

**On the ICM HPC:**

- The lab-share venv at
  `/network/iss/debette/users/mathieu.poirier/.venv` with vLLM installed via
  `uv add vllm` (see `pipeline_app_hpc/README.md`). vLLM pulls in vendored
  CUDA libraries (NCCL, cuBLAS, cuDNN) under
  `lib/python3.12/site-packages/nvidia/*/lib`.
- A pre-populated Hugging Face cache at
  `/network/iss/debette/users/mathieu.poirier/hf-cache/huggingface` containing
  `unsloth/gemma-4-31b-it-unsloth-bnb-4bit`. The sbatch script forces
  offline mode (`HF_HUB_OFFLINE=1`), so missing weights here would fail the
  job rather than silently downloading.
- A workdir + log directory at
  `/network/iss/debette/users/mathieu.poirier/csvd-hpc/{,/logs}`.

**Wired up on app boot** (`pipeline_app_hpc/main.py:395-401`):

```python
config = load_config()
ssh_master = SshControlMaster(alias=config.ssh_alias, socket_path=...)
vllm_server = VllmServer(ssh=ssh_master, config=config)
tuning_runner = TuningRunner(lock, vllm_server)
pipeline_runner = PipelineRunner(lock, vllm_server)
```

`VllmServer` is the **single owner of vLLM-on-HPC state**. The HPC card,
the header chip, and both runners all subscribe to its snapshot — they
never touch SSH or SLURM directly.

## Click "Start vLLM" → green "Ready" chip

Eleven steps from a click in the browser to "Ready" in the chip. The
state machine moves IDLE → SUBMITTED → ALLOCATED → READY, and at any point
can fail to FAILED (see [Failure modes](#failure-modes)).

```text
UI         VllmServer       SSH master       login node       compute node
 │              │                │                │                │
 │ click Start  │                │                │                │
 ├─────────────►│                │                │                │
 │              │ render sbatch  │                │                │
 │              │ open() ───────►│ ssh -M -N -f   │                │
 │              │                ├───────────────►│ (auth/MFA)     │
 │              │ mkdir + write  │                │                │
 │              │ heredoc ──────►│ ──────────────►│                │
 │              │ sbatch ───────►│ ──────────────►│ "Submitted N"  │
 │              │ state=SUBMIT   │                │                │
 │              │                │                │                │
 │              │ poll squeue ──►│ ──────────────►│ RUNNING gpu-N  │
 │              │ tail .err  ───►│ ──────────────►│ "##VLLM_PORT=" │
 │              │ -L 30800 ─────►│ ──────────────►│                │
 │              │                │  forward open  │                │
 │              │ state=ALLOC    │                │                │
 │              │                │                │ vllm serve     │
 │              │ GET /v1/models │   (via tunnel) │   loads 17 GB  │
 │              │ ──────────────────────────────────────────────►  │
 │              │                │                │   200 OK       │
 │              │ state=READY    │                │                │
 │ chip green   │                │                │                │
 │◄─────────────┤                │                │                │
```

### 1. UI handler fires

`pipeline_app_hpc/components/hpc_card.py:48-56` — the button's `on_click`
awaits `server.start()`. A `ui.notify` reminds you to complete any SSH
prompts in the terminal, since `ssh -f` will block silently if MFA is
waiting.

### 2. Lock + idempotency guard

`pipeline_app_hpc/hpc/lifecycle.py:304-318` — `VllmServer.start()` takes
its `asyncio.Lock`, then bails immediately if the current state is
already SUBMITTED, ALLOCATED, READY, or DRAINING. A second click is a
no-op, not an error. If a prior run failed and left a poller task alive,
it's drained before spawning a new one — otherwise repeated failure +
retry would leak a background task each time.

### 3. Render the sbatch template

`lifecycle.py:328` calls `_render_template(config)`, which reads
`pipeline_app_hpc/sbatch/vllm_serve.sbatch.j2` and substitutes nine
placeholders (`account`, `partition`, `qos`, `time_limit`, `cpus_per_task`,
`mem`, `log_dir`, `venv_path`, `hf_home`). It is **not** Jinja2 —
`_render_template` does literal `text.replace(...)` and validates each
value against `_SAFE_SBATCH_VALUE_RE` first, so a stray space or shell
metacharacter in `config.json` raises rather than injecting an `#SBATCH`
directive (`lifecycle.py:165-203`).

### 4. Open the SSH ControlMaster

`lifecycle.py:330` → `pipeline_app_hpc/hpc/ssh.py:93-141`. If the socket
file at `~/.cache/csvd-hpc/icm-hpc.sock` already exists and `ssh -O check`
returns 0, the existing master is reused. Otherwise a fresh master is
spawned:

```text
ssh -M -N -f -S <socket> \
    -o ControlPersist=10m \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes \
    icm-hpc
```

`-M` makes it the master; `-N` says no remote command; `-f` backgrounds
after authentication. **This is the moment when an MFA prompt or
passphrase request would appear in your launching terminal.** A 120 s
timeout (`SSH_MASTER_OPEN_TIMEOUT_SECONDS`) catches the case where
something is silently waiting for input.

### 5. Make the log dir + write the sbatch (in parallel)

`lifecycle.py:336-343` runs `mkdir -p $log_dir` and the heredoc sbatch
write concurrently via `asyncio.gather`. The two operations touch
disjoint paths and both go through the same SSH master (multiplexed), so
gathering halves the round-trip wait.

The heredoc write itself (`rsync_sbatch_template`,
`lifecycle.py:115-142`) builds one `bash -lc` command of the form:

```bash
set -e
mkdir -p '/network/iss/.../csvd-hpc'
cat > '/network/iss/.../csvd-hpc/vllm_serve.sbatch' << 'CSVD_HPC_EOF'
…rendered sbatch body…
CSVD_HPC_EOF
chmod +x '/network/iss/.../csvd-hpc/vllm_serve.sbatch'
```

`set -e` is essential: without it, a failed `cat` (e.g., NFS quota
exceeded) would still let the next `chmod +x` succeed and the corrupted
script would silently submit. The marker is `CSVD_HPC_EOF` plus a UUID
suffix if that string somehow appears in the rendered text.

### 6. Submit the job

`pipeline_app_hpc/hpc/sbatch.py:77-113` runs through the master:

```bash
cd '/network/iss/.../csvd-hpc' && sbatch \
    --export=ALL,VLLM_BASE_MODEL='unsloth/gemma-4-31b-it-unsloth-bnb-4bit',\
                VLLM_MAX_MODEL_LEN='16384',\
                VLLM_QUANTIZATION='bitsandbytes' \
    '/network/iss/.../csvd-hpc/vllm_serve.sbatch'
```

If you set `vllm_adapter_path`, three more `VLLM_ADAPTER_*` vars get
appended. Values containing commas or newlines are rejected up front —
SLURM splits `--export` entries on commas after shell parsing, so a
sneaky comma would split one variable into two.

The output `Submitted batch job 12345678` is parsed by a regex; the job
ID becomes the only handle the app keeps on the SLURM side.

### 7. State → SUBMITTED, start the poller

`lifecycle.py:350-353`. The snapshot publishes (`_publish` notifies all
subscribers, including the HPC card and header chip — chip turns blue
"Submitted"), and `_run_poller` starts on the asyncio event loop, polling
every 5 s.

### 8. The sbatch script does its setup on the compute node

Once SLURM allocates a node, the script body runs
(`pipeline_app_hpc/sbatch/vllm_serve.sbatch.j2`):

```bash
module purge
module load CUDA/12.4 cudnn/9.8.0.87-11-pewru6u gcc/12.4.0 python/3.12
source "/network/iss/.../.venv/bin/activate"

# Vendored CUDA libs (NCCL/cuBLAS/cuDNN) live in the venv after `uv sync`;
# `module load CUDA/12.4` does not add them to LD_LIBRARY_PATH.
for lib_dir in "$VIRTUAL_ENV"/lib/python3.12/site-packages/nvidia/*/lib; do
    [[ -d "$lib_dir" ]] && export LD_LIBRARY_PATH="$lib_dir:${LD_LIBRARY_PATH:-}"
done

export HF_HOME="/network/iss/.../hf-cache/huggingface"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export PYTHONUNBUFFERED=1
ulimit -n 65536

PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("",0));
                  p=s.getsockname()[1]; s.close(); print(p)')
echo "##VLLM_PORT=$PORT##" >&2

trap 'kill -TERM "$VLLM_PID"; wait "$VLLM_PID" 2>/dev/null || true' TERM

srun --cpu-bind=cores --mem-bind=local \
    vllm serve "${VLLM_ARGS[@]}" &
VLLM_PID=$!
wait "$VLLM_PID" || true
```

Three things worth pinning down here:

- **The vendored CUDA loop is load-bearing.** `module load CUDA/12.4`
  brings CUDA into PATH but not the NCCL/cuBLAS/cuDNN libs that vLLM ships
  in its wheel. Without the `LD_LIBRARY_PATH` walk, vLLM would pick
  up the wrong (or no) NCCL and tensor-parallel init would deadlock.
- **The port is chosen on the compute node, not the login node.** Python
  asks the kernel for any free TCP port, then prints it as a marker the
  Mac-side poller greps for. There is no fixed remote port — every job
  picks a different one.
- **Offline mode is non-negotiable.** Compute nodes have no internet; if
  the model isn't already in `HF_HOME`, vLLM exits within seconds with a
  clear error in the `.err` log.

`VLLM_ARGS` ends up as something like:

```text
unsloth/gemma-4-31b-it-unsloth-bnb-4bit
  --port <PORT> --host 0.0.0.0
  --gpu-memory-utilization 0.95
  --max-model-len 16384
  --dtype bfloat16
  --disable-log-requests
  --quantization bitsandbytes --load-format bitsandbytes
  [--enable-lora --max-lora-rank 16 --lora-modules svd=<adapter_path>]
```

### 9. Poller detects RUNNING + node + port

`lifecycle.py:464-511`. Each tick (5 s) the poller does, in parallel:

```python
info, prefetched_tail = await asyncio.gather(
    get_job_info(self._ssh, job_id),               # squeue
    fetch_log_tail(self._ssh, log_path, lines=100) # tail -n 100 .err
)
```

`get_job_info` runs `squeue --noheader -o '%i|%T|%R|%L|%M' --job <id>`
and parses one line into a `JobInfo` (state, node, time-left,
elapsed). When `state == "RUNNING"` and the log tail contains
`##VLLM_PORT=NNNNN##`, both halves of the handshake are in: SLURM has
told us the node, vLLM has told us the port.

If `squeue` returns no rows (the job has aged out of the queue), the
code falls back to `sacct` so it doesn't synthesize COMPLETED for a job
that actually crashed (`sbatch.py:154-175`).

### 10. Open the SSH `-L` tunnel

`pipeline_app_hpc/hpc/tunnel.py:33` → `ssh.py:217-252` runs through the
master with no need to spawn a new SSH process:

```bash
ssh -S <socket> -O forward -L 30800:gpu-ampere-NN:NNNNN icm-hpc
```

The forward is registered with the master; OpenSSH starts listening on
`127.0.0.1:30800` on your Mac. Any TCP connection to that port is
multiplexed back through the master to the login node and then forwarded
to `gpu-ampere-NN:NNNNN` on the compute node.

State → ALLOCATED, `local_url=http://127.0.0.1:30800` published on the
snapshot, and `_probe_ready` starts as a separate task.

### 11. Readiness probe waits for `/v1/models` to return 200

`pipeline_app_hpc/hpc/readiness.py:14-37`. From your Mac:

```python
async with httpx.AsyncClient(timeout=10) as client:
    while time.monotonic() < deadline:  # default 900s
        r = await client.get(f"{base_url}/v1/models")
        if r.status_code == 200:
            return
        await asyncio.sleep(5)
```

`base_url` is `http://127.0.0.1:30800` — every request rides the SSH
tunnel. The 900 s ceiling reflects the cold load of Gemma 4 31B at
4-bit: ~17 GB read from NFSv3 into A100 VRAM, typically 2-4 minutes
the first time the file is touched, fast subsequently when the NFS
page cache on the compute node is warm.

When `/v1/models` returns 200, the probe transitions ALLOCATED → READY,
guarded by `if self._snapshot.state == VllmServerState.ALLOCATED`
(`lifecycle.py:621`) so a concurrent stop doesn't get clobbered. The
chip turns green; the **Run Pipeline** button becomes useful.

## Click "Run Pipeline" → results JSON

The pipeline runs **as a subprocess on your Mac**, not on HPC. Only the
HTTP traffic to vLLM crosses the wire.

### 1. Refuse unless READY

`pipeline_app_hpc/runner.py:608-624`. `PipelineRunner.run()` reads
`vllm_server.snapshot` and refuses to start unless `state == READY` and
`local_url` is populated. The error message points back at the HPC card.

### 2. Build the subprocess env

`runner.py:410-463` constructs an explicit allowlist env (PATH, HOME, SSL
certs, plus all `VLLM_*` and `PIPELINE_*` overrides). The most important
entries:

```python
"VLLM_BASE_URL": "http://127.0.0.1:30800",     # the local tunnel
"VLLM_MODEL":    "svd"  if adapter else base,  # what /v1/chat/completions
                                               # asks for under "model"
"VLLM_BASE_MODEL_NAME": config.vllm_base_model,
"VLLM_ADAPTER_NAME":    config.vllm_adapter_name if adapter else "",
"VLLM_MAX_MODEL_LEN":   "16384",
"VLLM_QUANTIZATION":    "bitsandbytes",
# plus PIPELINE_CONFIDENCE_THRESHOLD, PIPELINE_RPM_LIMIT,
# PIPELINE_PROMPT_VERSION, …
```

Secrets (`NCBI_API_KEY`, `ENTREZ_EMAIL`) come from `.env` and only get
added if present.

### 3. Spawn `python -m pipeline_app_hpc.cli`

`runner.py:516-524` builds the argv as

```text
[python, "-m", "pipeline_app_hpc.cli",
 "--local-pdfs", config.local_pdfs_path, …]
```

and hands it to `asyncio.create_subprocess_exec` with `stdout=PIPE`,
`stderr=PIPE`, `cwd=project_root`, `env=env`, `start_new_session=True`
(own process group → killpg on cancel), and a 10 MB per-line read limit.

`pipeline_app_hpc/cli.py` constructs a `VllmProvider` from the env vars
and calls `pipeline_app_hpc.extract.run(...)`. Critically, it does **not**
call `pipeline/main.py` — the HPC stack has its own thin extract loop
that reuses the production code's prompt builder, JSON-schema parser,
rate limiter, and `PipelineConfig`, but bypasses PubMed search,
ClinicalTrials, and the database.

### 4. The provider talks to vLLM

`pipeline_app_hpc/providers/vllm_provider.py`:

- `_ensure_healthy()` (line 112) does one `GET /v1/models` with a 10 s
  timeout to fail fast if the tunnel is dead, before spending the full
  request budget on the first PDF.
- For each paper, `extract()` (line 132) sends `POST /v1/chat/completions`
  with a body that pins the deterministic settings vLLM needs:

  ```json
  {
    "model": "svd",
    "messages": [{"role": "system", "..." : "..."},
                 {"role": "user", "..." : "..."}],
    "max_tokens": 64000,
    "temperature": 0.0,
    "top_p": 1.0,
    "guided_json": { "...": "EXTRACTION_JSON_SCHEMA" },
    "guided_decoding_backend": "outlines"
  }
  ```

  `guided_json` is the load-bearing field: vLLM's **constrained
  decoding** forces every generated token to keep the string a valid
  prefix of the schema. Without it, the model would frequently emit
  almost-JSON that the Pydantic parser rejects.
- 429 / 5xx / transport errors trigger bounded retries with exponential
  backoff; truncation (`finish_reason == "length"`) raises an
  `ExtractionFailedError` immediately so the report flags it.

### 5. Stage markers stream back to the UI

The pipeline subprocess prints `##STAGE:extract##`, `##STAGE:batch_validate##`,
`##STAGE:report##` markers to stdout. `runner._handle_stdout`
(`runner.py:494-505`) sniffs for the literal `##STAGE:` prefix on every
line — cheap enough to run on the hot per-line path — and updates the
stage tracker without touching the log buffer.

When the subprocess exits, `find_newest_report` (`runner.py:181-186`)
locates the freshest `logs/json/pipeline_report_*.json` written after the
run started; that path is what the Results Viewer page opens.

## Click "Stop vLLM" → IDLE

`lifecycle.py:367-438`. Order matters because the SSH master is shared:

1. **State → DRAINING** up front so a concurrent `start()` bails out
   instead of racing two tunnels onto port 30800.
2. **Cancel the poller and the readiness probe in parallel.** They share
   no state, so cancelling them sequentially would just double the
   SIGTERM round-trip wait.
3. **`scancel <job_id>` and `tunnel.close()` in parallel** — SLURM
   cancellation hits the login node, tunnel close hits the master to
   remove the `-L` forward. Independent operations.
4. **`ssh.close()` last** — sends `ssh -O exit` to terminate the master.
   Must come after step 3 because the tunnel close uses the master to
   send `-O cancel`.
5. **State → IDLE.** A100 returns to the queue. If `scancel` raised, the
   state instead becomes FAILED with a message asking you to check
   `squeue --me` manually — the SLURM job may still be running.

`stop()` is also called automatically from
`main.build_shutdown_handler` (`main.py:51-91`) when you Ctrl+C the
NiceGUI app. The whole shutdown is wrapped in `asyncio.wait_for` with a
12 s wall-clock cap so a stuck SSH operation doesn't freeze the
terminal.

## Failure modes

The state machine has six terminal states and several ways to land in
FAILED. The HPC card surfaces the SLURM state and the last 100 lines of
the `.err` log so you can usually diagnose without leaving the GUI.

| Cause | What happens | UI shows |
| ----- | ------------ | -------- |
| MFA / passphrase prompt missed | `ssh -M -N -f` blocks 120 s, then `SshError` | FAILED, message about checking the terminal for prompts |
| SLURM never allocates within `vllm_time_limit` | squeue reports TIMEOUT | FAILED, last log tail |
| Job runs but vLLM crashes during weight load | squeue → COMPLETED while we're still SUBMITTED/ALLOCATED | FAILED: "SLURM job completed before vLLM was stopped" |
| Compute node has no network and `HF_HOME` missing the model | vLLM exits within seconds; `.err` shows a Hugging Face download error | FAILED, log tail explains |
| `/v1/models` never returns 200 within 900 s | `wait_until_ready` raises TimeoutError; orphan job auto-cancelled | FAILED, "vLLM at … not ready after 900s" |
| Tunnel open fails (port 30800 already bound on Mac) | `ssh -O forward` exits non-zero | FAILED: "tunnel open failed: …" |
| Admin pause (STOPPED / SUSPENDED) | Treated as failure rather than letting HTTP hang | FAILED |
| Job hits NODE_FAIL / OOM / PREEMPTED | Listed in `_SLURM_FAILED_STATES` (`lifecycle.py:36-54`) | FAILED with the exact state name |
| `scancel` itself fails during stop | State → FAILED with "scancel failed — job N may still be running" | Manual `squeue --me` recommended |

A clean time-limit shutdown is **not** treated as failure: the sbatch
script's `wait "$VLLM_PID" || true` swallows the SIGTERM exit code, and
the poller sees `info.state == "COMPLETED"` only when `state == DRAINING`
(user-initiated) or after a true crash.

## Manual verification cheatsheet

When something is wrong and the GUI isn't telling you enough, these are
the commands the lifecycle code is actually running. Run them yourself
to confirm each layer.

```bash
# Is the SSH master alive?
ssh -S ~/.cache/csvd-hpc/icm-hpc.sock -O check icm-hpc

# Force the master closed (e.g., MFA expired and re-auth needed):
ssh -S ~/.cache/csvd-hpc/icm-hpc.sock -O exit icm-hpc

# What's in the SLURM queue under your name?
ssh icm-hpc 'squeue --me'

# Tail the vLLM log for a specific job:
ssh icm-hpc 'tail -n 100 \
  /network/iss/debette/users/mathieu.poirier/csvd-hpc/logs/svd-vllm-<jobid>.err'

# Is the local tunnel actually passing traffic to vLLM?
curl http://127.0.0.1:30800/v1/models

# What did the last finished job report?
ssh icm-hpc 'sacct -j <jobid> --format=JobID,State,ExitCode,Elapsed,MaxRSS'

# Force-cancel a stuck job:
ssh icm-hpc 'scancel <jobid>'
```

If `curl /v1/models` works but the pipeline subprocess can't reach
vLLM, the env scrub is suspect — check `runner.build_extract_env` and
confirm `VLLM_BASE_URL` matches `vllm_local_port` in your `config.json`.
