# HPC Explorer

Static React visualization for a single ICM HPC compute-node probe.

HPC Explorer turns the sibling probe file
`../icm-hpc/probing_results/master_probe_RESULTS_GPU.txt` into a typed,
build-time snapshot and renders it as a dark, terminal-flavored operations page.
The current checked-in snapshot is for `gd-cortex / sphpc-gpu06`, job `2337208`,
probed on `2026-05-10 15:52:05 CEST`.

There is no backend. `scripts/parse-probe.ts` reads the text probe, runs the
parser in `src/lib/parse-probe.ts`, and writes the generated module
`src/data/probe.ts`.

## Current Snapshot

The parser currently emits 38 top-level fields, including:

- 4 NVIDIA A100 80GB PCIe GPUs
- 19 SLURM partitions
- 63 NFS mounts
- 32 filtered lmod module entries
- 12 vendored CUDA sublibraries missing from `LD_LIBRARY_PATH`

## What It Shows

A sticky telemetry bar stays pinned at the top with host identity, a live probe
clock, GPU cards, an NVLink mini-matrix, NUMA placement, queue status, partition
load, and a fairshare gauge.

Below the telemetry bar, eight scroll-revealed sections render the parsed probe:

1. **Identity & job context** - host, OS, kernel, uptime, SLURM allocation,
   account, QOS, visible GPUs, and cgroup CPU affinity.
2. **NVLink topology** - d3-force SVG graph for GPU/GPU/NIC links, a
   connectivity matrix, edge hover descriptions, and click-to-isolate nodes.
3. **CPU & NUMA architecture** - two-socket NUMA diagram, memory use, PCIe
   device map, network interfaces, cgroup affinity, and process limits.
4. **CUDA + PyTorch stack** - driver-to-fine-tuning stack tower, PyTorch/CUDA
   status, BF16 support, vendored CUDA loader-path warning, Python environment,
   and Hugging Face cache location.
5. **Modules & toolchain** - filtered available modules grouped by package name,
   loaded GCC/Python/uv/NVIDIA versions, and fine-tuning library versions.
6. **Partition explorer** - sortable and filterable partition table, active
   partition highlight, GPU resource counts, drained/down nodes, and pending
   job reasons.
7. **QOS, fairshare & jobs** - QOS small multiples, active QOS highlight,
   account associations, fairshare context, and current user jobs.
8. **Storage atlas** - user-relevant paths, local XFS mounts, storage
   environment variables, and searchable NFS mounts grouped by server.

## Stack

- Vite 8, React 19, TypeScript 6
- Tailwind CSS v4 through `@tailwindcss/vite` and CSS `@theme` tokens
- `motion` for reveal, counter, and panel animation
- `d3-force` for the NVLink topology layout
- Radix Tooltip for accessible hover content
- Local Inter and JetBrains Mono fonts from `@fontsource`

## Commands

Run from `hpc-explorer/`:

```bash
npm install
npm run dev      # parse the probe, then serve at http://localhost:5173
npm run build    # parse, type-check, and write static assets to dist/
npm run preview  # serve the built dist/ bundle
npm run lint     # ESLint over the app and parser
npm run parse    # regenerate src/data/probe.ts only
```

`predev` and `prebuild` both run `npm run parse` automatically, so the dev
server and production build start from the latest probe text.

## Keyboard

The page supports a small Vim-style chord navigator:

| Chord | Action |
| --- | --- |
| `g h` | Jump to top |
| `g 1` ... `g 8` | Jump to section 1 through 8 |

Keyboard navigation is ignored while typing in inputs such as the partition and
NFS filters.

## Project Layout

- `scripts/parse-probe.ts` - build-time CLI that reads the probe and writes
  `src/data/probe.ts`
- `src/lib/parse-probe.ts` - section-by-section parser for the box-drawing probe
  tables
- `src/lib/types.ts` - `ClusterSnapshot` and related TypeScript interfaces
- `src/data/probe.ts` - generated snapshot module imported by the app
- `src/App.tsx` - page composition: telemetry bar, eight sections, footer
- `src/components/primitives/` - Panel, KV, Bar, Sparkline, Badge, Counter,
  ScrambleText, Tooltip, and Section primitives
- `src/components/telemetry/` - sticky telemetry bar widgets
- `src/components/sections/` - the eight main explorer sections
- `src/viz/` - NVLink force graph, NUMA socket diagram, and CUDA stack tower
- `src/hooks/` - probe clock, reduced-motion, reveal, boot-sequence, and section
  navigation hooks
- `src/styles/globals.css` - Tailwind import, local fonts, design tokens,
  base styles, and animation keyframes
- `public/favicon.svg` - app favicon copied into builds

## Data Contract

The parser keys off exact section titles in
`../icm-hpc/probing_results/master_probe_RESULTS_GPU.txt`. If the probe script
renames a section, update the matching `get(...)` call in
`src/lib/parse-probe.ts`.

The generated `src/data/probe.ts` is typed as `ClusterSnapshot` and imported
directly by `src/App.tsx`. Runtime rendering therefore assumes the parse step
has already succeeded.

## Live Clock

The probe was captured once at `meta.probedAt`. The UI advances a display clock
from that anchor so the identity strip feels alive when the page is open. That
clock is cosmetic; the rest of the dashboard is the static parsed snapshot.

## Updating the Snapshot

After overwriting
`../icm-hpc/probing_results/master_probe_RESULTS_GPU.txt` with a fresh probe,
run:

```bash
npm run parse
```

Then run `npm run dev` or `npm run build` as usual. If parsing fails, first
compare the failing section title against the exact strings expected in
`src/lib/parse-probe.ts`.
