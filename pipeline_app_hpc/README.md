# cSVD HPC Pipeline App

A localhost-orchestrated NiceGUI front-end (port `8081`) that runs the
gene-extraction pipeline against **Gemma 4 31B served by vLLM on the ICM HPC**.

This stack is independent of `pipeline_app/` (the production GUI on port 8080).
Both can run simultaneously.

## Usage

### One-time setup

1. Add vLLM to the lab share venv on HPC:

   ```bash
   ssh icm-hpc
   cd /network/iss/debette/users/mathieu.poirier
   module purge && module load CUDA/12.4 cudnn/9.8.0.87-11-pewru6u gcc/12.4.0 python/3.12
   uv add vllm
   ```

2. Confirm `~/.ssh/config` has an `icm-hpc` alias pointing at
   `sphpc-login02.icm-institute.org`.

3. Create the workdir on HPC:

   ```bash
   ssh icm-hpc 'mkdir -p /network/iss/debette/users/mathieu.poirier/csvd-hpc/logs'
   ```

### Run the GUI

```bash
python -m pipeline_app_hpc.main
```

Opens at <http://127.0.0.1:8081>.

### Workflow

1. **Configure & Run** → set the local PDF path and any vLLM/SSH config; click
   **Save Settings**.
2. In the **vLLM on HPC** card, click **Start vLLM**. Wait until the chip
   turns green ("Ready"). First load takes 2–4 minutes (cold NFS read).
3. Click **Run Pipeline**. Stage tracker shows progress.
4. When done, **Stop vLLM** to release the V100.

## Tests

```bash
pytest tests/pipeline_app_hpc/                       # unit tests
RUN_HPC_INTEGRATION=1 pytest tests/pipeline_app_hpc/ # includes live SLURM submit
```

## Constraints

- **Local PDFs only** — no PubMed search, no clinical-trials fetch.
- **No PostgreSQL writes** — JSON reports (`logs/json/pipeline_report_*.json`)
  are the deliverable.
- **Existing `pipeline/` and `pipeline_app/` are read-only** — this stack
  imports them but never modifies them.

See [`docs/superpowers/specs/2026-05-09-hpc-vllm-pipeline-stack-design.md`](../docs/superpowers/specs/2026-05-09-hpc-vllm-pipeline-stack-design.md)
for the full design.
