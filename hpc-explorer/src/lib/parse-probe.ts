// =============================================================================
// PROBE PARSER
// Reads the box-drawing text from master_probe_RESULTS_GPU.txt and produces
// a strongly-typed ClusterSnapshot.
// =============================================================================

import type {
  ClusterSnapshot,
  Meta,
  SlurmJobContext,
  SlurmNodeView,
  ComputeHost,
  Cpu,
  Memory,
  JobLimits,
  Gpu,
  GpuTopology,
  NvLinkStatus,
  NetIface,
  PciHca,
  BlockDevice,
  NfsMount,
  XfsMount,
  UserPath,
  ModuleEntry,
  Toolchain,
  PythonEnv,
  FinetuneLib,
  PytorchCuda,
  VendoredCuda,
  VendoredCudaLib,
  SchedulerConfig,
  Partition,
  GpuResource,
  DrainedNode,
  PendingReason,
  ClusterQueue,
  AccountAssoc,
  QosLimit,
  Fairshare,
  MyJob,
  StorageEnvVar,
  ProcLimits,
  TopoLink,
} from "./types";
import { parseMemoryGB } from "./format";
import { TOPO_LINKS } from "./topology";

// --- low-level table extraction -----------------------------------------------

interface ParsedTable {
  headers: string[];
  rows: string[][];
}

const isBoxTop = (line: string) => /^[┌╔]/.test(line.trimStart());
const isBoxBot = (line: string) => /^[└╚]/.test(line.trimStart());
const isBoxSep = (line: string) => /^[├╠]/.test(line.trimStart());
const isBoxRow = (line: string) => /^[│║]/.test(line.trimStart());

function splitRow(line: string): string[] {
  const trimmed = line.trim();
  const inner = trimmed.replace(/^[│║]/, "").replace(/[│║]$/, "");
  return inner.split(/[│║]/).map((c) => c.trim());
}

function parseTable(lines: string[]): ParsedTable | null {
  const top = lines.findIndex(isBoxTop);
  if (top < 0) return null;
  const sep = lines.findIndex((l, i) => i > top && isBoxSep(l));
  const bot = lines.findIndex((l, i) => i > top && isBoxBot(l));
  if (sep < 0 || bot < 0) return null;

  const headerLines: string[] = [];
  for (let i = top + 1; i < sep; i++) {
    if (isBoxRow(lines[i])) headerLines.push(lines[i]);
  }
  const headers = headerLines.length > 0 ? splitRow(headerLines[0]) : [];

  const rows: string[][] = [];
  for (let i = sep + 1; i < bot; i++) {
    if (isBoxRow(lines[i])) rows.push(splitRow(lines[i]));
  }

  return { headers, rows };
}

// --- section index ------------------------------------------------------------

interface Section {
  title: string;
  lines: string[];
}

function indexSections(text: string): {
  headerLines: string[];
  sections: Map<string, Section>;
} {
  const allLines = text.split(/\r?\n/);
  const sections = new Map<string, Section>();
  const headerLines: string[] = [];

  let firstSection = -1;
  for (let i = 0; i < allLines.length; i++) {
    if (allLines[i].trim() === "SLURM Job Context") {
      firstSection = i;
      break;
    }
  }
  if (firstSection < 0) {
    throw new Error("Could not locate first section 'SLURM Job Context'");
  }
  for (let i = 0; i < firstSection; i++) headerLines.push(allLines[i]);

  const isSectionHeader = (l: string): boolean => {
    if (l.length === 0) return false;
    if (isBoxTop(l) || isBoxRow(l) || isBoxSep(l) || isBoxBot(l)) return false;
    // Top-level section: starts at column 0
    if (!l.startsWith(" ")) return true;
    // Sub-section: indented `  ↳ <title>`
    if (/^\s+↳\s+/.test(l)) return true;
    return false;
  };

  let cursor = firstSection;
  while (cursor < allLines.length) {
    const title = allLines[cursor].trim();
    let next = cursor + 1;
    while (next < allLines.length) {
      if (isSectionHeader(allLines[next]) && allLines[next].trim() !== title) break;
      next++;
    }
    sections.set(title, { title, lines: allLines.slice(cursor + 1, next) });
    cursor = next;
  }

  return { headerLines, sections };
}

// --- helpers ------------------------------------------------------------------

const toInt = (s: string | undefined, fallback = 0): number => {
  if (s == null) return fallback;
  const n = parseInt(s.replace(/[^\d-]/g, ""), 10);
  return Number.isFinite(n) ? n : fallback;
};
const toFloat = (s: string | undefined, fallback = 0): number => {
  if (s == null) return fallback;
  const m = s.match(/-?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : fallback;
};
const findFirst = (s: string, re: RegExp): string => {
  const m = s.match(re);
  return m && m[1] ? m[1] : "";
};

// --- header -------------------------------------------------------------------

function parseHeader(headerLines: string[]): Meta {
  const all = headerLines.join("\n");
  const cluster = findFirst(all, /cluster\s+([^\s·]+)/);
  const userName = findFirst(all, /user\s+([^\s]+)\s*\(uid=\d+\)/);
  const uidStr = findFirst(all, /uid=(\d+)/);
  const host = findFirst(all, /host\s+([^\s·\n]+)/);
  const jobId = findFirst(all, /job\s+(\d+)/);
  const slurmVersion = findFirst(all, /slurm\s+([^\s·]+)/);
  const account = findFirst(all, /account\s+([^\s·]+)/);
  const os = findFirst(all, /·\s+(Rocky Linux[^·\n]+?)\s+·/);
  const kernel = findFirst(all, /kernel\s+([^\s\n]+)/);
  const probedAt = findFirst(all, /(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+)/);

  return {
    cluster,
    host,
    user: { name: userName, uid: toInt(uidStr) },
    jobId,
    slurmVersion,
    account,
    os,
    kernel,
    probedAt,
  };
}

// --- section parsers ----------------------------------------------------------

function tableOf(section: Section): ParsedTable {
  const t = parseTable(section.lines);
  if (!t) throw new Error(`No table found in section: ${section.title}`);
  return t;
}

function rowMap(t: ParsedTable): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of t.rows) if (r.length >= 2) out[r[0]] = r[1];
  return out;
}

function parseSlurmJobContext(s: Section): SlurmJobContext {
  const m = rowMap(tableOf(s));
  return {
    jobId: m["Job ID"] ?? "",
    jobName: m["Job name"] ?? "",
    partition: m["Partition"] ?? "",
    account: m["Account"] ?? "",
    qos: m["QOS"] ?? "",
    nodes: m["Node(s)"] ?? "",
    gpusOnNode: toInt(m["GPUs on node"] ?? "0"),
    cpusOnNode: toInt(m["CPUs on node"] ?? "0"),
    memPerNode: m["Memory/node"] ?? "",
    cudaVisible: m["$CUDA_VISIBLE_DEVICES"] ?? "",
  };
}

function parseSlurmNodeView(s: Section): SlurmNodeView {
  const m = rowMap(tableOf(s));
  return {
    state: m["State"] ?? "",
    partitions: (m["Partitions"] ?? "")
      .split(",")
      .map((p) => p.trim())
      .filter(Boolean),
    cpuTot: toInt(m["CPUTot"] ?? "0"),
    realMemoryMB: toInt(m["RealMemory"] ?? "0"),
    gres: m["Gres"] ?? "",
    availableFeatures: m["AvailableFeatures"] ?? "",
    cfgTres: m["CfgTRES"] ?? "",
    allocTres: m["AllocTRES"] ?? "",
    weight: toInt(m["Weight"] ?? "0"),
  };
}

function parseComputeHost(s: Section): ComputeHost {
  const m = rowMap(tableOf(s));
  return {
    hostname: m["Hostname"] ?? "",
    os: m["OS"] ?? "",
    kernel: m["Kernel"] ?? "",
    uptime: m["Uptime"] ?? "",
    logicalCpus: toInt(m["Logical CPUs"] ?? "0"),
  };
}

function parseCpu(s: Section): Cpu {
  const m = rowMap(tableOf(s));
  return {
    model: m["Model"] ?? "",
    sockets: toInt(m["Sockets"] ?? "0"),
    coresPerSocket: toInt(m["Cores/socket"] ?? "0"),
    threadsPerCore: toInt(m["Threads/core"] ?? "0"),
    logicalCpus: toInt(m["Logical CPUs"] ?? "0"),
    numaNodes: toInt(m["NUMA nodes"] ?? "0"),
  };
}

function parseMemory(s: Section): Memory {
  const m = rowMap(tableOf(s));
  return {
    totalGB: parseMemoryGB(m["Total RAM"] ?? "0"),
    availableGB: parseMemoryGB(m["Available"] ?? "0"),
    freeGB: parseMemoryGB(m["Free"] ?? "0"),
    swap: m["Swap"] ?? "",
    shmGB: parseMemoryGB(m["/dev/shm (tmpfs)"] ?? "0"),
  };
}

function parseJobLimits(s: Section): JobLimits {
  const m = rowMap(tableOf(s));
  return {
    cpuAffinityMask: m["CPU affinity mask"] ?? "",
    cgroupMembership: m["Cgroup membership"] ?? "",
  };
}

function parseGpus(s: Section): Gpu[] {
  return tableOf(s).rows.map((r) => {
    const [memUsed = "", memTotal = ""] = (r[2] ?? "").split("/").map((x) => x.trim());
    return {
      idx: toInt(r[0]),
      name: r[1] ?? "",
      memUsed,
      memTotal,
      capability: r[5] ?? "",
    };
  });
}

function parseGpuTopology(s: Section): GpuTopology {
  const t = tableOf(s);
  const gpuCols = t.headers
    .map((h, i) => ({ h, i }))
    .filter(({ h }) => /^GPU\d+|^NIC\d+/.test(h));
  const numaAffinityIdx = t.headers.indexOf("NUMA Affinity");

  const matrix: Record<string, Record<string, TopoLink>> = {};
  const numaAffinity: Record<string, number | null> = {};

  for (const row of t.rows) {
    const name = row[0];
    const inner: Record<string, TopoLink> = {};
    for (const { h, i } of gpuCols) {
      const raw = row[i] ?? "X";
      inner[h] = TOPO_LINKS.has(raw as TopoLink) ? (raw as TopoLink) : "X";
    }
    matrix[name] = inner;
    if (numaAffinityIdx >= 0) {
      const v = row[numaAffinityIdx]?.trim();
      numaAffinity[name] = v ? toInt(v) : null;
    }
  }

  return { matrix, numaAffinity };
}

function parseNvLinkStatus(s: Section): NvLinkStatus[] {
  return tableOf(s).rows.map((r) => ({
    gpu: toInt(r[0]),
    links: toInt(r[1]),
    speedGBs: toFloat(r[2]),
  }));
}

function parseNetwork(s: Section): NetIface[] {
  return tableOf(s).rows.map((r) => ({
    iface: r[0] ?? "",
    state: r[1] === "UP" ? "UP" : "DOWN",
    addr: r[2] ?? "",
  }));
}

function parsePciHcas(s: Section): PciHca[] {
  return tableOf(s).rows.map((r) => ({
    slot: r[0] ?? "",
    class: r[1] ?? "",
    vendorDevice: r[2] ?? "",
  }));
}

function parseBlockDevices(s: Section): BlockDevice[] {
  return tableOf(s).rows.map((r) => ({
    name: r[0] ?? "",
    size: r[1] ?? "",
    kind: r[2] ?? "",
    bus: r[3] ?? "",
    model: r[4] ?? "",
    serial: r[5] ?? "",
  }));
}

function parseNfsMounts(s: Section): NfsMount[] {
  return tableOf(s).rows.map((r) => {
    const [rsize = "", wsize = ""] = (r[3] ?? "").split("/").map((x) => x.trim());
    return {
      mount: r[0] ?? "",
      server: r[1] ?? "",
      vers: toInt(r[2]),
      rsize,
      wsize,
      sec: r[4] ?? "",
    };
  });
}

function parseXfsMounts(s: Section): XfsMount[] {
  return tableOf(s).rows.map((r) => ({
    mount: r[0] ?? "",
    block: r[1] ?? "",
    inode: r[2] ?? "",
    sector: r[3] ?? "",
    ags: toInt(r[4]),
    logBlocks: r[5] ?? "",
  }));
}

function parseUserPaths(s: Section): UserPath[] {
  return tableOf(s).rows.map((r) => {
    const [inodesFree = "", inodesTotal = ""] = (r[5] ?? "").split("/").map((x) => x.trim());
    return {
      path: r[0] ?? "",
      fs: r[1] ?? "",
      free: r[2] ?? "",
      total: r[3] ?? "",
      usePct: toInt(r[4] ?? "0"),
      inodesFree,
      inodesTotal,
      rw: r[6] ?? "",
      perms: r[7] ?? "",
    };
  });
}

function parseModules(s: Section): ModuleEntry[] {
  return tableOf(s).rows.map((r) => ({ name: r[0] ?? "", version: r[1] ?? "" }));
}

function parseToolchain(s: Section): Toolchain {
  const m = rowMap(tableOf(s));
  return {
    gcc: m["gcc"] ?? "",
    python: m["Python"] ?? "",
    uv: m["uv"] ?? "",
    nvidiaDriver: m["NVIDIA driver"] ?? "",
  };
}

function parsePythonEnv(s: Section): PythonEnv {
  const m = rowMap(tableOf(s));
  return {
    interpreter: m["Interpreter"] ?? "",
    sysPrefix: m["sys.prefix"] ?? "",
    sysBasePrefix: m["sys.base_prefix"] ?? "",
    venvActive: (m["Venv active"] ?? "").toLowerCase() === "yes",
    venvPath: m[".venv path"] ?? "",
    projectDir: m["Project dir"] ?? "",
    pyprojectToml: m["pyproject.toml"] ?? "",
    uvLock: m["uv.lock"] ?? "",
  };
}

function parseFinetuneLibs(s: Section): FinetuneLib[] {
  return tableOf(s).rows.map((r) => ({ pkg: r[0] ?? "", version: r[1] ?? "" }));
}

function parsePytorchCuda(s: Section): PytorchCuda {
  const m = rowMap(tableOf(s));
  return {
    torchVersion: m["torch.__version__"] ?? "",
    cudaVersion: m["torch.version.cuda"] ?? "",
    torchImport: m["torch import"] ?? "",
    cudaAvailable: (m["cuda.is_available()"] ?? "").toLowerCase() === "yes",
    deviceCount: toInt(m["cuda.device_count()"] ?? "0"),
    bf16: (m["cuda.is_bf16_supported()"] ?? "").toLowerCase() === "yes",
    cudnn: m["backends.cudnn.version()"] ?? "",
    nccl: m["cuda.nccl.version()"] ?? "",
  };
}

function parseVendoredCuda(s: Section): VendoredCuda {
  const t = tableOf(s);
  const libs: VendoredCudaLib[] = t.rows.map((r) => ({
    sublib: r[0] ?? "",
    nSos: toInt(r[1]),
    libPath: r[2] ?? "",
    inLdPath: (r[3] ?? "").includes("✓"),
  }));
  const rootLine = s.lines.find((l) => /^\s*root:\s*/.test(l)) ?? "";
  const root = rootLine.replace(/^\s*root:\s*/, "").trim();
  return { libs, root, warning: libs.some((l) => !l.inLdPath) };
}

function parseSchedulerKVPairs(s: Section): Partial<SchedulerConfig> {
  const out: Record<string, string> = {};
  for (const line of s.lines) {
    const m = line.match(/^\s+(\w+)\s*=\s*(.+)$/);
    if (m) out[m[1]] = m[2].trim();
  }
  return {
    jobAcctGatherType: out["JobAcctGatherType"] ?? "",
    jobContainerType: out["JobContainerType"] ?? "",
    maxJobCount: toInt(out["MaxJobCount"] ?? "0"),
    stateSaveLocation: out["StateSaveLocation"] ?? "",
  };
}

function parseClusterSchedulerConfig(
  s: Section,
  prev: Partial<SchedulerConfig>,
): SchedulerConfig {
  const m = rowMap(tableOf(s));
  return {
    clusterName: m["ClusterName"] ?? "",
    schedulerType: m["SchedulerType"] ?? "",
    selectType: m["SelectType"] ?? "",
    selectTypeParameters: m["SelectTypeParameters"] ?? "",
    maxJobCount: toInt(m["MaxJobCount"] ?? `${prev.maxJobCount ?? 0}`),
    accountingStorageType: m["AccountingStorageType"] ?? "",
    priorityType: m["PriorityType"] ?? "",
    preemptType: m["PreemptType"] ?? "",
    jobAcctGatherType: prev.jobAcctGatherType ?? "",
    jobContainerType: prev.jobContainerType ?? "",
    stateSaveLocation: prev.stateSaveLocation ?? "",
  };
}

function parsePartitions(s: Section): Partition[] {
  return tableOf(s).rows.map((r) => {
    const nodes = (r[3] ?? "0/0/0/0").split("/").map((x) => toInt(x));
    const cpus = (r[4] ?? "0/0/0/0").split("/").map((x) => toInt(x));
    return {
      name: r[0] ?? "",
      avail: r[1] ?? "",
      timeLimit: r[2] ?? "",
      nodes: {
        alloc: nodes[0] ?? 0,
        idle: nodes[1] ?? 0,
        other: nodes[2] ?? 0,
        total: nodes[3] ?? 0,
      },
      cpus: {
        alloc: cpus[0] ?? 0,
        idle: cpus[1] ?? 0,
        other: cpus[2] ?? 0,
        total: cpus[3] ?? 0,
      },
      cpuUsePct: toInt(r[5] ?? "0"),
      memPerNode: r[6] ?? "",
      gres: r[7] ?? "",
    };
  });
}

function parseGpuResources(s: Section): GpuResource[] {
  return tableOf(s).rows.map((r) => ({
    partition: r[0] ?? "",
    type: r[1] ?? "",
    used: toInt(r[2]),
    total: toInt(r[3]),
    free: toInt(r[4]),
    utilPct: toInt(r[5] ?? "0"),
  }));
}

function parseDrainedNodes(s: Section): DrainedNode[] {
  return tableOf(s).rows.map((r) => ({
    node: r[0] ?? "",
    state: r[1] ?? "",
    since: r[2] ?? "",
    by: r[3] ?? "",
    reason: r[4] ?? "",
  }));
}

function parsePendingReasons(s: Section): PendingReason[] {
  return tableOf(s).rows.map((r) => ({
    reason: r[0] ?? "",
    jobs: toInt(r[1]),
  }));
}

function parseClusterQueue(s: Section): ClusterQueue {
  const m = rowMap(tableOf(s));
  return {
    running: toInt(m["Running"] ?? "0"),
    pending: toInt(m["Pending"] ?? "0"),
    other: toInt(m["Other"] ?? "0"),
    total: toInt(m["Total"] ?? "0"),
  };
}

function parseAccountAssoc(s: Section): AccountAssoc[] {
  return tableOf(s).rows.map((r) => ({
    account: r[0] ?? "",
    partition: r[1] ?? "",
    defQos: r[2] ?? "",
    nQos: toInt(r[3]),
    maxJobs: r[4] ?? "",
    maxWall: r[5] ?? "",
  }));
}

function parseQosLimits(s: Section): QosLimit[] {
  return tableOf(s).rows.map((r) => ({
    qos: r[0] ?? "",
    maxWall: r[1] ?? "",
    maxTresJob: r[2] ?? "",
    maxTresUser: r[3] ?? "",
    maxJobsUser: r[4] ?? "",
    prio: toInt(r[5]),
  }));
}

function parseFairshare(s: Section): Fairshare {
  const r = tableOf(s).rows[0] ?? [];
  return {
    user: r[0] ?? "",
    account: r[1] ?? "",
    rawShares: toInt(r[2]),
    rawUsage: r[3] ?? "",
    effUsagePct: toFloat(r[4] ?? "0"),
    fairshare: toFloat(r[5] ?? "0"),
  };
}

function parseMyJobs(s: Section): MyJob[] {
  return tableOf(s).rows.map((r) => ({
    jobId: r[0] ?? "",
    partition: r[1] ?? "",
    name: r[2] ?? "",
    state: r[3] ?? "",
    elapsed: r[4] ?? "",
    limit: r[5] ?? "",
    nodes: toInt(r[6]),
    nodesOrReason: r[7] ?? "",
  }));
}

function parseStorageEnvVars(s: Section): StorageEnvVar[] {
  return tableOf(s).rows.map((r) => ({
    variable: r[0] ?? "",
    value: r[1] ?? "",
  }));
}

function parseProcLimits(s: Section): ProcLimits {
  const m = rowMap(tableOf(s));
  return {
    openFiles: m["open files (-n)"] ?? "",
    fileSize: m["file size (-f)"] ?? "",
    lockedMemoryKB: m["locked memory KB (-l)"] ?? "",
  };
}

// --- top-level ---------------------------------------------------------------

export function parseProbe(text: string): ClusterSnapshot {
  const { headerLines, sections } = indexSections(text);

  const get = (name: string): Section => {
    const s = sections.get(name);
    if (!s) throw new Error(`Section not found: ${name}`);
    return s;
  };
  const getOpt = (name: string): Section | null => sections.get(name) ?? null;
  // Some section titles embed runtime data (hostname, username). Match by
  // prefix so the parser survives probes from other nodes/users.
  const getByPrefix = (prefix: string): Section => {
    for (const [k, v] of sections) {
      if (k.startsWith(prefix)) return v;
    }
    throw new Error(`Section not found by prefix: ${prefix}`);
  };

  const ioSection = getOpt("I/O Snapshot");
  const ioSnapshot =
    ioSection?.lines.find((l) => /\(/.test(l))?.trim() ?? "(no I/O recorded)";

  const hfSection = get("Hugging Face Cache & Offline Flags");
  const hfMap = rowMap(tableOf(hfSection));

  const schedulerKv = parseSchedulerKVPairs(
    get("SLURM scheduler config (storage-related)"),
  );
  const schedulerConfig = parseClusterSchedulerConfig(
    get("Cluster Scheduler Config  (scontrol show config, filtered)"),
    schedulerKv,
  );

  return {
    meta: parseHeader(headerLines),
    slurmJobContext: parseSlurmJobContext(get("SLURM Job Context")),
    slurmNodeView: parseSlurmNodeView(getByPrefix("Slurm Node View")),
    computeHost: parseComputeHost(get("Compute Host")),
    cpu: parseCpu(get("CPU")),
    memory: parseMemory(get("Memory")),
    jobLimits: parseJobLimits(get("Job-Effective Limits  (taskset + cgroup)")),
    gpus: parseGpus(get("GPUs  (nvidia-smi --query-gpu)")),
    gpuTopology: parseGpuTopology(get("GPU Topology  (nvidia-smi topo -m)")),
    nvlinkStatus: parseNvLinkStatus(get("NVLink status  (nvidia-smi nvlink -s)")),
    network: parseNetwork(get("Network Interfaces  (ip -br addr; lo skipped)")),
    pciHCAs: parsePciHcas(get("PCI HCAs  (lspci | NVIDIA / Mellanox / Ethernet)")),
    blockDevices: parseBlockDevices(
      get("Block Devices  (local disks only — partitions hidden)"),
    ),
    nfsMounts: parseNfsMounts(get("NFS Mount Details  (version, rsize/wsize, sec)")),
    xfsMounts: parseXfsMounts(
      get("XFS Per-Mount Info  (block & inode sizes, AG count, log)"),
    ),
    userPaths: parseUserPaths(get("User-Relevant Paths  (non-existent paths skipped)")),
    ioSnapshot,
    modules: parseModules(
      get(
        "Available Modules  (module --terse avail | filtered to cuda|python|gcc|cudnn|nccl)",
      ),
    ),
    toolchain: parseToolchain(get("Toolchain Versions")),
    pythonEnv: parsePythonEnv(get("Python Environment")),
    finetuneLibs: parseFinetuneLibs(
      get("Fine-Tuning Libraries  (versions via importlib.metadata)"),
    ),
    pytorchCuda: parsePytorchCuda(get("PyTorch / CUDA Bridge")),
    vendoredCuda: parseVendoredCuda(
      get(
        "Vendored CUDA Loader Path  (none in LD_LIBRARY_PATH — torch may fail to dlopen)",
      ),
    ),
    hfCache: { hfHome: hfMap["HF_HOME"] ?? "" },
    schedulerConfig,
    partitions: parsePartitions(
      get("Partitions  (nodes & CPUs: Alloc/Idle/Other/Total)"),
    ),
    gpuResources: parseGpuResources(
      get("GPU / Generic Resources  (excludes down & drained nodes)"),
    ),
    drainedNodes: parseDrainedNodes(get("Drained / Down Nodes")),
    pendingReasons: parsePendingReasons(get("Pending Job Reasons")),
    clusterQueue: parseClusterQueue(get("Cluster Queue")),
    accountAssoc: parseAccountAssoc(
      get("Your Account Associations  (QOS limits in next section)"),
    ),
    qosLimits: parseQosLimits(getByPrefix("QOS Limits")),
    fairshare: parseFairshare(get("Fairshare / Priority")),
    myJobs: parseMyJobs(get("Your Jobs")),
    storageEnvVars: parseStorageEnvVars(get("Storage Env Vars")),
    procLimits: parseProcLimits(get("Process Limits  (this shell)")),
  };
}
