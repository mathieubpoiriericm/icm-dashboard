// =============================================================================
// CLUSTER SNAPSHOT TYPES
// One probe of one ICM HPC compute node, fully typed.
// =============================================================================

export type TopoLink = "X" | "NV12" | "SYS" | "NODE" | "PHB" | "PXB" | "PIX";

export interface Meta {
  cluster: string;
  host: string;
  user: { name: string; uid: number };
  jobId: string;
  slurmVersion: string;
  account: string;
  os: string;
  kernel: string;
  probedAt: string;
}

export interface SlurmJobContext {
  jobId: string;
  jobName: string;
  partition: string;
  account: string;
  qos: string;
  nodes: string;
  gpusOnNode: number;
  cpusOnNode: number;
  memPerNode: string;
  cudaVisible: string;
}

export interface SlurmNodeView {
  state: string;
  partitions: string[];
  cpuTot: number;
  realMemoryMB: number;
  gres: string;
  availableFeatures: string;
  cfgTres: string;
  allocTres: string;
  weight: number;
}

export interface ComputeHost {
  hostname: string;
  os: string;
  kernel: string;
  uptime: string;
  logicalCpus: number;
}

export interface Cpu {
  model: string;
  sockets: number;
  coresPerSocket: number;
  threadsPerCore: number;
  logicalCpus: number;
  numaNodes: number;
}

export interface Memory {
  totalGB: number;
  availableGB: number;
  freeGB: number;
  swap: string;
  shmGB: number;
}

export interface JobLimits {
  cpuAffinityMask: string;
  cgroupMembership: string;
}

export interface Gpu {
  idx: number;
  name: string;
  memUsed: string;
  memTotal: string;
  capability: string;
}

export interface GpuTopology {
  matrix: Record<string, Record<string, TopoLink>>;
  numaAffinity: Record<string, number | null>;
}

export interface NvLinkStatus {
  gpu: number;
  links: number;
  speedGBs: number;
}

export interface NetIface {
  iface: string;
  state: "UP" | "DOWN";
  addr: string;
}

export interface PciHca {
  slot: string;
  class: string;
  vendorDevice: string;
}

export interface BlockDevice {
  name: string;
  size: string;
  kind: string;
  bus: string;
  model: string;
  serial: string;
}

export interface NfsMount {
  mount: string;
  server: string;
  vers: number;
  rsize: string;
  wsize: string;
  sec: string;
}

export interface XfsMount {
  mount: string;
  block: string;
  inode: string;
  sector: string;
  ags: number;
  logBlocks: string;
}

export interface UserPath {
  path: string;
  fs: string;
  free: string;
  total: string;
  usePct: number;
  inodesFree: string;
  inodesTotal: string;
  rw: string;
  perms: string;
}

export interface ModuleEntry {
  name: string;
  version: string;
}

export interface Toolchain {
  gcc: string;
  python: string;
  uv: string;
  nvidiaDriver: string;
}

export interface PythonEnv {
  interpreter: string;
  sysPrefix: string;
  sysBasePrefix: string;
  venvActive: boolean;
  venvPath: string;
  projectDir: string;
  pyprojectToml: string;
  uvLock: string;
}

export interface FinetuneLib {
  pkg: string;
  version: string;
}

export interface PytorchCuda {
  torchVersion: string;
  cudaVersion: string;
  torchImport: string;
  cudaAvailable: boolean;
  deviceCount: number;
  bf16: boolean;
  cudnn: string;
  nccl: string;
}

export interface VendoredCudaLib {
  sublib: string;
  nSos: number;
  libPath: string;
  inLdPath: boolean;
}

export interface VendoredCuda {
  libs: VendoredCudaLib[];
  root: string;
  warning: boolean;
}

export interface SchedulerConfig {
  clusterName: string;
  schedulerType: string;
  selectType: string;
  selectTypeParameters: string;
  maxJobCount: number;
  accountingStorageType: string;
  priorityType: string;
  preemptType: string;
  jobAcctGatherType: string;
  jobContainerType: string;
  stateSaveLocation: string;
}

export interface Partition {
  name: string;
  avail: string;
  timeLimit: string;
  nodes: { alloc: number; idle: number; other: number; total: number };
  cpus: { alloc: number; idle: number; other: number; total: number };
  cpuUsePct: number;
  memPerNode: string;
  gres: string;
}

export interface GpuResource {
  partition: string;
  type: string;
  used: number;
  total: number;
  free: number;
  utilPct: number;
}

export interface DrainedNode {
  node: string;
  state: string;
  since: string;
  by: string;
  reason: string;
}

export interface PendingReason {
  reason: string;
  jobs: number;
}

export interface ClusterQueue {
  running: number;
  pending: number;
  other: number;
  total: number;
}

export interface AccountAssoc {
  account: string;
  partition: string;
  defQos: string;
  nQos: number;
  maxJobs: string;
  maxWall: string;
}

export interface QosLimit {
  qos: string;
  maxWall: string;
  maxTresJob: string;
  maxTresUser: string;
  maxJobsUser: string;
  prio: number;
}

export interface Fairshare {
  user: string;
  account: string;
  rawShares: number;
  rawUsage: string;
  effUsagePct: number;
  fairshare: number;
}

export interface MyJob {
  jobId: string;
  partition: string;
  name: string;
  state: string;
  elapsed: string;
  limit: string;
  nodes: number;
  nodesOrReason: string;
}

export interface StorageEnvVar {
  variable: string;
  value: string;
}

export interface ProcLimits {
  openFiles: string;
  fileSize: string;
  lockedMemoryKB: string;
}

export interface ClusterSnapshot {
  meta: Meta;
  slurmJobContext: SlurmJobContext;
  slurmNodeView: SlurmNodeView;
  computeHost: ComputeHost;
  cpu: Cpu;
  memory: Memory;
  jobLimits: JobLimits;
  gpus: Gpu[];
  gpuTopology: GpuTopology;
  nvlinkStatus: NvLinkStatus[];
  network: NetIface[];
  pciHCAs: PciHca[];
  blockDevices: BlockDevice[];
  nfsMounts: NfsMount[];
  xfsMounts: XfsMount[];
  userPaths: UserPath[];
  ioSnapshot: string;
  modules: ModuleEntry[];
  toolchain: Toolchain;
  pythonEnv: PythonEnv;
  finetuneLibs: FinetuneLib[];
  pytorchCuda: PytorchCuda;
  vendoredCuda: VendoredCuda;
  hfCache: { hfHome: string };
  schedulerConfig: SchedulerConfig;
  partitions: Partition[];
  gpuResources: GpuResource[];
  drainedNodes: DrainedNode[];
  pendingReasons: PendingReason[];
  clusterQueue: ClusterQueue;
  accountAssoc: AccountAssoc[];
  qosLimits: QosLimit[];
  fairshare: Fairshare;
  myJobs: MyJob[];
  storageEnvVars: StorageEnvVar[];
  procLimits: ProcLimits;
}
